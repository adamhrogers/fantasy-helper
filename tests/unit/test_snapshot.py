import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from fantasy_helper.adapters import _http
from fantasy_helper.domain.ports import Alert, AlertPriority
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.pipeline.snapshot import SnapshotResult, SnapshotSet, run_snapshot
from fantasy_helper.storage.raw_store import LocalRawStore, RawStoreUnavailableError


def espn_players(n: int) -> dict[str, object]:
    return {
        "players": [
            {"id": i, "player": {"id": i, "stats": [{"statSourceId": 1, "statSplitTypeId": 0}]}}
            for i in range(n)
        ]
    }


def espn_game_logs(n: int) -> dict[str, object]:
    game = {"statSourceId": 0, "statSplitTypeId": 5, "seasonId": 2027, "externalId": "401"}
    return {"players": [{"id": i, "player": {"id": i, "stats": [game]}} for i in range(n)]}


def sleeper_rows(n: int) -> list[dict[str, object]]:
    return [{"player_id": str(i), "stats": {"pts": 10.0}} for i in range(n)]


HEALTHY: dict[str, object] = {
    "sleeper.state": {"season": "2026", "week": 3, "season_type": "regular"},
    "sleeper.players": {str(i): {"player_id": str(i)} for i in range(1200)},
    "sleeper.projections_season": sleeper_rows(400),
    "sleeper.projections_week": sleeper_rows(400),
    "espn.season": {"currentScoringPeriod": {"id": 18}},
    "espn.players_projections": espn_players(400),
    "espn.game_logs": espn_game_logs(400),
    "espn.pro_schedule": {"settings": {"proTeams": [{"id": i} for i in range(30)]}},
    "espn.athletes": {
        "items": [{"id": str(i), "dateOfBirth": "2000-01-01T08:00Z"} for i in range(500)]
    },
}


SEEN_DEPTHS: list[int] = []
WAITS: list[float] = []


def route(request: httpx.Request) -> str:
    url = str(request.url)
    if "sleeper.app/v1/state/nba" in url:
        return "sleeper.state"
    if "sleeper.app/v1/players/nba" in url:
        return "sleeper.players"
    if re.search(r"projections/nba/\d{4}/\d+", url):
        return "sleeper.projections_week"
    if re.search(r"projections/nba/\d{4}\?", url):
        return "sleeper.projections_season"
    if "sports.core.api.espn.com" in url:
        return "espn.athletes"
    if "leaguedefaults" in url:
        depth = json.loads(request.headers["x-fantasy-filter"])["players"][
            "filterStatsForTopScoringPeriodIds"
        ]["value"]
        SEEN_DEPTHS.append(depth)
        return "espn.players_projections" if depth == 1 else "espn.game_logs"
    if "proTeamSchedules_wl" in url:
        return "espn.pro_schedule"
    if re.search(r"fba/seasons/\d{4}$", url.split("?")[0]):
        return "espn.season"
    raise AssertionError(f"unexpected request {url}")


class FakeNotifier:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


def run(
    tmp_path: Path,
    run_set: SnapshotSet = SnapshotSet.MORNING,
    overrides: dict[str, object] | None = None,
    record_fails: bool = False,
    **options: Any,
) -> tuple[SnapshotResult, FakeNotifier, list[RunRecord], LocalRawStore]:
    responses = HEALTHY | (overrides or {})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        answer = responses[route(request)]
        if callable(answer):  # a function of the call count: fail first, then recover
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    recorded: list[RunRecord] = []

    def record(run_record: RunRecord) -> None:
        if record_fails:
            raise RuntimeError("database down")
        recorded.append(run_record)

    notifier = FakeNotifier()
    store = options.pop("store", None) or LocalRawStore(tmp_path / "raw")
    with _http.make_client(transport=httpx.MockTransport(handler)) as client:
        result = run_snapshot(
            run_set,
            client=client,
            store=store,
            notifier=notifier,
            record=record,
            network_wait_seconds=0,
            sleep=options.pop("sleep", WAITS.append),  # records the retry waits, never sleeps
            **options,
        )
    return result, notifier, recorded, store


def test_morning_snapshot_saves_every_dataset(tmp_path: Path) -> None:
    result, notifier, recorded, store = run(tmp_path)
    assert result.run.status is RunStatus.SUCCEEDED
    labels = {r.label for r in result.results}
    assert labels == set(HEALTHY)
    started, succeeded = notifier.alerts  # quiet run events only
    assert {started.priority, succeeded.priority} == {AlertPriority.LOW}
    assert started.title == "Morning snapshot started"
    assert succeeded.title == "Morning snapshot succeeded"
    assert "9/9 datasets saved" in succeeded.message
    assert len(recorded) == 1
    assert recorded[0].kind is RunKind.SNAPSHOT
    espn = next(r for r in result.results if r.label == "espn.players_projections")
    assert espn.key is not None
    assert json.loads(store.get(espn.key)) == HEALTHY["espn.players_projections"]
    assert store.meta(espn.key)["validation"] == "ok"
    assert espn.items == 400


def test_only_backfills_box_scores_at_the_requested_depth(tmp_path: Path) -> None:
    SEEN_DEPTHS.clear()
    result, _, _, store = run(tmp_path, only=frozenset({"espn.game_logs"}), game_log_depth=30)
    assert {r.label for r in result.results} == {"sleeper.state", "espn.game_logs"}
    assert SEEN_DEPTHS == [30]
    logs = next(r for r in result.results if r.label == "espn.game_logs")
    assert logs.key is not None
    assert store.meta(logs.key)["validation"] == "ok"


def test_evening_snapshot_skips_the_once_a_day_datasets(tmp_path: Path) -> None:
    result, _, _, _ = run(tmp_path, SnapshotSet.EVENING)
    labels = {r.label for r in result.results}
    assert labels == {
        "sleeper.state",
        "espn.season",
        "espn.players_projections",
        "sleeper.projections_week",
    }


def test_a_failing_dataset_degrades_the_run_and_alerts(tmp_path: Path) -> None:
    result, notifier, recorded, _ = run(
        tmp_path, overrides={"espn.players_projections": httpx.Response(503)}
    )
    assert result.run.status is RunStatus.DEGRADED
    assert recorded[0].errors == ("espn.players_projections: HTTP 503 (attempts: 3)",)
    alert = notifier.alerts[-1]
    assert alert.title == "Morning snapshot incomplete"
    assert alert.priority is AlertPriority.HIGH
    assert "espn.players_projections: HTTP 503" in alert.message


def test_a_suspiciously_small_response_is_saved_but_flagged(tmp_path: Path) -> None:
    result, notifier, _, store = run(
        tmp_path, overrides={"espn.players_projections": espn_players(10)}
    )
    espn = next(r for r in result.results if r.label == "espn.players_projections")
    assert espn.key is not None  # kept as evidence of what changed
    assert "only 10 players" in (espn.error or "")
    assert "only 10 players" in store.meta(espn.key)["validation"]
    assert notifier.alerts[-1].priority is AlertPriority.HIGH


def test_quiet_events_can_be_switched_off(tmp_path: Path) -> None:
    _, notifier, _, _ = run(tmp_path, progress=False)
    assert notifier.alerts == []  # a healthy run says nothing without progress events
    _, notifier, _, _ = run(
        tmp_path / "2", overrides={"espn.season": httpx.Response(503)}, progress=False
    )
    # retries and failures are always sent: two retry notices, then the failure
    assert [a.priority for a in notifier.alerts] == [
        AlertPriority.DEFAULT, AlertPriority.DEFAULT, AlertPriority.HIGH,
    ]  # fmt: skip


def test_week_projections_are_skipped_when_state_fails(tmp_path: Path) -> None:
    result, _, _, _ = run(tmp_path, overrides={"sleeper.state": httpx.Response(500)})
    week = next(r for r in result.results if r.label == "sleeper.projections_week")
    assert "Sleeper week unknown" in (week.error or "")
    season = next(r for r in result.results if r.label == "sleeper.projections_season")
    assert season.ok  # falls back to the season label from the calendar


def test_everything_failing_is_urgent(tmp_path: Path) -> None:
    down: dict[str, object] = {label: httpx.ConnectError("offline") for label in HEALTHY}
    result, notifier, _, _ = run(tmp_path, overrides=down)
    assert result.run.status is RunStatus.FAILED
    assert notifier.alerts[-1].priority is AlertPriority.URGENT
    assert notifier.alerts[-1].title == "Morning snapshot failed"


def test_raw_files_survive_a_database_outage(tmp_path: Path) -> None:
    result, notifier, _, _ = run(tmp_path, record_fails=True)
    assert result.recorded is False
    assert all(r.key for r in result.results)
    assert "run log not written" in notifier.alerts[-1].message


def test_a_player_pull_that_fills_the_limit_is_flagged() -> None:
    from fantasy_helper.adapters.espn import public

    full = espn_game_logs(public.PLAYER_LIMIT)
    assert public.validate_game_logs(full) is not None
    assert public.validate_game_logs(espn_game_logs(public.PLAYER_LIMIT - 100)) is None


def fails_then_recovers(failures: int, good: object) -> object:
    calls = {"n": 0}

    def answer() -> object:
        calls["n"] += 1
        # get_raw tries each request 3 times; fail every try of the first `failures` attempts
        return httpx.Response(503) if calls["n"] <= 3 * failures else good

    return answer


def test_a_server_error_is_retried_alone_and_recovers(tmp_path: Path) -> None:
    WAITS.clear()
    result, notifier, _, _ = run(
        tmp_path,
        overrides={"espn.game_logs": fails_then_recovers(1, HEALTHY["espn.game_logs"])},
    )
    assert result.run.status is RunStatus.SUCCEEDED
    logs = next(r for r in result.results if r.label == "espn.game_logs")
    assert (logs.ok, logs.attempts) == (True, 2)
    assert WAITS == [120.0]
    titles = [a.title for a in notifier.alerts]
    assert titles == [
        "Morning snapshot started",
        "Morning snapshot: retrying (attempt 2 of 3)",
        "Morning snapshot succeeded",
    ]
    retrying = notifier.alerts[1]
    assert retrying.priority is AlertPriority.DEFAULT
    assert "espn.game_logs: the source's servers erred" in retrying.message
    assert "Trying again in 2 min." in retrying.message
    others = [r for r in result.results if r.label != "espn.game_logs"]
    assert all(r.attempts == 1 for r in others)  # nothing that worked is fetched twice


def test_a_lasting_outage_gives_up_after_three_attempts(tmp_path: Path) -> None:
    WAITS.clear()
    result, notifier, _, _ = run(tmp_path, overrides={"espn.season": httpx.Response(503)})
    season = next(r for r in result.results if r.label == "espn.season")
    assert (season.ok, season.attempts) == (False, 3)
    assert WAITS == [120.0, 300.0]
    final = notifier.alerts[-1]
    assert final.title == "Morning snapshot incomplete"
    assert final.priority is AlertPriority.HIGH
    assert "the source's servers erred, after 3 attempts" in final.message


def test_failures_a_retry_cant_fix_fail_at_once(tmp_path: Path) -> None:
    WAITS.clear()
    result, notifier, _, _ = run(tmp_path, overrides={"espn.athletes": httpx.Response(404)})
    athletes = next(r for r in result.results if r.label == "espn.athletes")
    assert (athletes.ok, athletes.attempts) == (False, 1)
    assert WAITS == []  # no waiting on something that can't change
    assert not any("retrying" in a.title for a in notifier.alerts)
    assert "not retried (retrying can't fix it)" in notifier.alerts[-1].message


def test_a_flagged_once_a_day_dataset_is_never_fetched_again(tmp_path: Path) -> None:
    calls = {"n": 0}

    def small_player_list() -> object:
        calls["n"] += 1
        return {"1": {"player_id": "1"}}  # suspiciously small: saved, but flagged

    WAITS.clear()
    result, _, _, _ = run(tmp_path, overrides={"sleeper.players": small_player_list})
    players = next(r for r in result.results if r.label == "sleeper.players")
    assert players.key is not None  # kept as evidence
    assert players.error is not None  # flagged
    assert calls["n"] == 1  # Sleeper asks for at most one request a day


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


MORNING = datetime(2026, 11, 7, 11, 17, tzinfo=UTC)


def never_twice() -> object:
    raise AssertionError("fetched Sleeper's player list twice in one day")


def test_the_player_list_is_fetched_once_a_day_across_machines(tmp_path: Path) -> None:
    """The laptop and the cloud both run the morning set while they overlap (ADR 0023)."""
    earlier = MORNING - timedelta(minutes=13)
    LocalRawStore(tmp_path / "raw").put(
        source="sleeper", dataset="players", fetched_at=earlier, body=b"{}", meta={}
    )
    result, notifier, _, _ = run(
        tmp_path, overrides={"sleeper.players": never_twice}, clock=FixedClock(MORNING)
    )
    players = next(r for r in result.results if r.label == "sleeper.players")
    assert (players.ok, players.reused) == (True, True)
    assert players.key is not None
    assert players.key.startswith("sleeper/players/2026-11-07/")
    assert result.run.status is RunStatus.SUCCEEDED
    assert "1 already saved today by another run" in notifier.alerts[-1].message


def test_yesterdays_player_list_doesnt_count(tmp_path: Path) -> None:
    LocalRawStore(tmp_path / "raw").put(
        source="sleeper", dataset="players", fetched_at=MORNING - timedelta(days=1), body=b"{}",
        meta={},
    )  # fmt: skip
    result, _, _, _ = run(tmp_path, clock=FixedClock(MORNING))
    players = next(r for r in result.results if r.label == "sleeper.players")
    assert (players.ok, players.reused, players.items) == (True, False, 1200)


class FlakyStore(LocalRawStore):
    """R2 is unreachable for the first write of one dataset."""

    def __init__(self, root: Path, dataset: str) -> None:
        super().__init__(root)
        self.failing = {dataset}

    def put(
        self,
        *,
        source: str,
        dataset: str,
        fetched_at: datetime,
        body: bytes,
        meta: Mapping[str, object],
    ) -> str:
        if dataset in self.failing:
            self.failing.discard(dataset)
            raise RawStoreUnavailableError("R2 unreachable (write: ConnectTimeoutError)")
        return super().put(
            source=source, dataset=dataset, fetched_at=fetched_at, body=body, meta=meta
        )


def test_an_r2_outage_while_saving_is_retried(tmp_path: Path) -> None:
    WAITS.clear()
    store = FlakyStore(tmp_path / "raw", "players_projections")
    result, notifier, _, _ = run(tmp_path, store=store)
    espn = next(r for r in result.results if r.label == "espn.players_projections")
    assert (espn.ok, espn.attempts) == (True, 2)
    assert WAITS == [120.0]
    assert "the raw store (R2) was unreachable" in notifier.alerts[1].message
