import json
import re
from pathlib import Path

import httpx
import pytest

from fantasy_helper.adapters import _http
from fantasy_helper.domain.ports import Alert, AlertPriority
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.pipeline.snapshot import SnapshotResult, SnapshotSet, run_snapshot
from fantasy_helper.storage.raw_store import LocalRawStore


def espn_players(n: int) -> dict[str, object]:
    return {
        "players": [
            {"id": i, "player": {"id": i, "stats": [{"statSourceId": 1, "statSplitTypeId": 0}]}}
            for i in range(n)
        ]
    }


def sleeper_rows(n: int) -> list[dict[str, object]]:
    return [{"player_id": str(i), "stats": {"pts": 10.0}} for i in range(n)]


HEALTHY: dict[str, object] = {
    "sleeper.state": {"season": "2026", "week": 3, "season_type": "regular"},
    "sleeper.players": {str(i): {"player_id": str(i)} for i in range(1200)},
    "sleeper.projections_season": sleeper_rows(400),
    "sleeper.projections_week": sleeper_rows(400),
    "espn.season": {"currentScoringPeriod": {"id": 18}},
    "espn.players_projections": espn_players(400),
    "espn.pro_schedule": {"settings": {"proTeams": [{"id": i} for i in range(30)]}},
}


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
    if "leaguedefaults" in url:
        return "espn.players_projections"
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
) -> tuple[SnapshotResult, FakeNotifier, list[RunRecord], LocalRawStore]:
    responses = HEALTHY | (overrides or {})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200)
        answer = responses[route(request)]
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

    notifier, store = FakeNotifier(), LocalRawStore(tmp_path / "raw")
    with _http.make_client(transport=httpx.MockTransport(handler)) as client:
        result = run_snapshot(
            run_set,
            client=client,
            store=store,
            notifier=notifier,
            record=record,
            network_wait_seconds=0,
        )
    return result, notifier, recorded, store


def test_morning_snapshot_saves_every_dataset(tmp_path: Path) -> None:
    result, notifier, recorded, store = run(tmp_path)
    assert result.run.status is RunStatus.SUCCEEDED
    labels = {r.label for r in result.results}
    assert labels == set(HEALTHY)
    assert notifier.alerts == []
    assert len(recorded) == 1
    assert recorded[0].kind is RunKind.SNAPSHOT
    espn = next(r for r in result.results if r.label == "espn.players_projections")
    assert espn.key is not None
    assert json.loads(store.get(espn.key)) == HEALTHY["espn.players_projections"]
    assert store.meta(espn.key)["validation"] == "ok"
    assert espn.items == 400


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
    assert recorded[0].errors == ("espn.players_projections: HTTP 503",)
    (alert,) = notifier.alerts
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
    assert notifier.alerts


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
    assert notifier.alerts[0].priority is AlertPriority.URGENT
    assert "failed" in notifier.alerts[0].title


def test_raw_files_survive_a_database_outage(tmp_path: Path) -> None:
    result, notifier, _, _ = run(tmp_path, record_fails=True)
    assert result.recorded is False
    assert all(r.key for r in result.results)
    assert "run log not written" in notifier.alerts[0].message
