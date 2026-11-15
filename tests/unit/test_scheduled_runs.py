"""The scheduled runs (`fh run daily|weekly`, `fh ingest`): they wait for this morning's snapshot,
take turns, ping the watchdog, and exit non-zero for anything but a clean run (ADR 0025, 0026)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fantasy_helper.adapters import cloud_run
from fantasy_helper.cli import _runs
from fantasy_helper.cli import ingest as cli_ingest
from fantasy_helper.cli import run as cli_run
from fantasy_helper.cli._common import REPORTED_EXIT
from fantasy_helper.cli.main import app
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.runs import FailureKind, RunRecord, RunStatus
from fantasy_helper.domain.users import Role, User
from fantasy_helper.pipeline.daily import DailyResult
from fantasy_helper.pipeline.freshness import mark_snapshot_complete
from fantasy_helper.pipeline.ingest import IngestReport, LeagueResult
from fantasy_helper.pipeline.weekly import WeeklyResult
from fantasy_helper.storage import crypto
from fantasy_helper.storage.raw_store import LocalRawStore

runner = CliRunner()
OWNER = User(email="a@example.com", display_name="A", timezone="America/New_York", role=Role.OWNER)


class Beat:
    def __init__(self) -> None:
        self.pings: list[str] = []

    def start(self) -> None:
        self.pings.append("start")

    def success(self, detail: str = "") -> None:
        self.pings.append("success")

    def fail(self, detail: str = "") -> None:
        self.pings.append("fail")


class Harness:
    """Everything a run touches outside its own logic, replaced by recorders."""

    def __init__(self) -> None:
        self.beat = Beat()
        self.slugs: list[str] = []
        self.sleeps: list[float] = []
        self.locked = 0  # transactions that took the run lock
        self.logged: list[RunRecord] = []
        self.pipeline_calls = 0


def league(name: str = "L") -> League:
    return League(
        user_id=OWNER.id, platform=Platform.SLEEPER, external_id="1", season=2027, name=name
    )


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    harness = Harness()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@localhost/x")
    monkeypatch.setenv("FH_ENCRYPTION_KEY", crypto.generate_key())
    monkeypatch.setenv("FH_DATA_DIR", str(tmp_path / "data"))

    @contextmanager
    def locked_transaction(_url: str) -> Iterator[object]:
        harness.locked += 1
        yield object()

    @contextmanager
    def no_client() -> Iterator[None]:
        yield None

    def heartbeat(_settings: object, slug: str) -> Beat:
        harness.slugs.append(slug)
        return harness.beat

    for module in (cli_run, cli_ingest):
        monkeypatch.setattr(module, "pipeline_transaction", locked_transaction)
        monkeypatch.setattr(module, "make_client", no_client)
        monkeypatch.setattr(module.users, "get_owner", lambda _session: OWNER)
        monkeypatch.setattr(module, "_sleep", harness.sleeps.append)
    monkeypatch.setattr(cli_run, "heartbeat_from_settings", heartbeat)
    monkeypatch.setattr(cli_run, "record_run_safely", lambda _url, run: harness.logged.append(run))
    monkeypatch.setattr(cli_ingest, "_log_run", lambda _url, run: harness.logged.append(run))
    return harness


def no_snapshot_yet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = LocalRawStore(tmp_path / "empty")  # a different folder from snapshot_from's
    monkeypatch.setattr(cli_run, "open_raw_store", lambda _s: empty)


def snapshot_from(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, at: datetime) -> None:
    """A store holding the marker a complete morning snapshot leaves (ADR 0028)."""
    store = LocalRawStore(tmp_path / "raw")
    mark_snapshot_complete(store, run_set="morning", datasets=["espn.game_logs"], at=at)
    monkeypatch.setattr(cli_run, "open_raw_store", lambda _s: store)


def today() -> datetime:
    return datetime.now(UTC)


def clean_daily(harness: Harness) -> Any:
    def run(*_a: object, **_k: object) -> DailyResult:
        harness.pipeline_calls += 1
        return DailyResult(ingest=IngestReport())

    return run


def test_a_scheduled_daily_run_works_from_this_mornings_snapshot(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert h.pipeline_calls == 1
    assert h.locked == 1  # it took its turn behind any other run
    assert h.slugs == ["daily"]
    assert h.beat.pings == ["start", "success"]
    assert h.logged[0].status is RunStatus.SUCCEEDED


def test_a_scheduled_run_waits_for_a_late_snapshot_then_fails_loudly(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_snapshot_yet(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert h.pipeline_calls == 0  # nothing was built from stale data
    assert h.sleeps == [120.0, 300.0]  # two waits, three attempts
    assert h.beat.pings == ["start", "fail"]
    assert h.logged[0].status is RunStatus.FAILED
    assert h.locked == 0  # it never got as far as the database


def test_a_snapshot_that_arrives_during_the_wait_is_picked_up(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = LocalRawStore(tmp_path / "late")
    monkeypatch.setattr(cli_run, "open_raw_store", lambda _s: store)
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))

    def arrives(seconds: float) -> None:
        h.sleeps.append(seconds)
        mark_snapshot_complete(store, run_set="morning", datasets=[], at=today())

    monkeypatch.setattr(cli_run, "_sleep", arrives)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert h.sleeps == [120.0]
    assert h.pipeline_calls == 1


def test_by_hand_a_run_doesnt_insist_on_todays_snapshot(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_snapshot_yet(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    result = runner.invoke(app, ["run", "daily"])
    assert result.exit_code == 0, result.output
    assert h.pipeline_calls == 1
    assert h.sleeps == []


def test_a_degraded_daily_run_exits_reported_and_pings_fail(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())

    def degraded(*_a: object, **_k: object) -> DailyResult:
        report = IngestReport(leagues=[LeagueResult(league=league(), failure=FailureKind.AUTH)])
        return DailyResult(ingest=report)

    monkeypatch.setattr(cli_run, "run_daily", degraded)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert h.beat.pings == ["start", "fail"]
    assert h.logged[0].status is RunStatus.DEGRADED


def test_a_crash_after_the_run_still_alerts_and_pings_fail(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli_run, "write_report_page", boom)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT  # alert_crash told the phone, which is what 3 means
    assert h.beat.pings == ["start", "fail"]


def test_a_bug_inside_the_run_stops_it_without_retrying(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())

    def bug(*_a: object, **_k: object) -> DailyResult:
        raise KeyError("oops")

    monkeypatch.setattr(cli_run, "run_daily", bug)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert h.sleeps == []  # a bug isn't retried: retrying can't fix it
    assert h.beat.pings == ["start", "fail"]
    assert h.logged[0].status is RunStatus.FAILED


def test_the_weekly_run_has_the_same_gate_lock_and_watchdog(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())

    def weekly(*_a: object, **_k: object) -> WeeklyResult:
        h.pipeline_calls += 1
        return WeeklyResult(ingest=IngestReport())

    monkeypatch.setattr(cli_run, "run_weekly", weekly)
    result = runner.invoke(app, ["run", "weekly", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert (h.slugs, h.locked, h.pipeline_calls) == (["weekly"], 1, 1)
    assert h.beat.pings == ["start", "success"]

    no_snapshot_yet(monkeypatch, tmp_path)
    stale = runner.invoke(app, ["run", "weekly", "--require-snapshot-today"])
    assert stale.exit_code == REPORTED_EXIT
    assert h.pipeline_calls == 1  # no second run


def test_ingest_takes_its_turn_and_a_failure_exits_reported(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_ingest, "open_raw_store", lambda _s: LocalRawStore(tmp_path / "raw"))

    def clean(*_a: object, **_k: object) -> IngestReport:
        h.pipeline_calls += 1
        return IngestReport()

    monkeypatch.setattr(cli_ingest, "run_ingest", clean)
    ok = runner.invoke(app, ["ingest"])
    assert ok.exit_code == 0, ok.output
    assert h.locked == 1
    assert h.beat.pings == []  # a hand-started ingest isn't on the watchdog (no stray check)

    def bug(*_a: object, **_k: object) -> IngestReport:
        raise KeyError("oops")

    monkeypatch.setattr(cli_ingest, "run_ingest", bug)
    failed = runner.invoke(app, ["ingest"])
    assert failed.exit_code == REPORTED_EXIT
    assert h.logged[-1].status is RunStatus.FAILED


def with_next_job(
    monkeypatch: pytest.MonkeyPatch, started: list[str], *, fails: bool = False
) -> None:
    """Make this run the middle of a chain: FH_NEXT_JOB is set, and starting the next job is
    recorded (or refused) instead of calling Google."""
    monkeypatch.setenv(
        "FH_NEXT_JOB", "projects/fantasy-helper-123456/locations/us-east4/jobs/weekly"
    )

    def starter(resource: str) -> None:
        if fails:
            raise cloud_run.CloudRunError("HTTP 403")
        started.append(resource)

    real = _runs.start_next
    monkeypatch.setattr(
        cli_run, "start_next",
        lambda settings, events, after: real(settings, events, after=after, starter=starter),
    )  # fmt: skip


def test_warnings_alone_dont_make_a_run_fail(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A condition that lasts (a Sleeper player not linked yet, a matchup spanning two weeks, a
    database nearing its quota) is a warning on the phone and DEGRADED in the log. It must not
    exit non-zero: that would raise a "down" alert every morning and bury the real failures."""
    snapshot_from(monkeypatch, tmp_path, today())

    def warned(*_a: object, **_k: object) -> DailyResult:
        league_ok = LeagueResult(league=league(), unresolved_players=2)  # loaded, with a warning
        return DailyResult(ingest=IngestReport(leagues=[league_ok]))

    monkeypatch.setattr(cli_run, "run_daily", warned)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert h.beat.pings == ["start", "success"]
    assert h.logged[0].status is RunStatus.DEGRADED  # the run log still says what it was

    def warned_week(*_a: object, **_k: object) -> WeeklyResult:
        league_ok = LeagueResult(league=league(), unresolved_players=2)
        return WeeklyResult(ingest=IngestReport(leagues=[league_ok]))

    monkeypatch.setattr(cli_run, "run_weekly", warned_week)
    weekly = runner.invoke(app, ["run", "weekly", "--require-snapshot-today"])
    assert weekly.exit_code == 0, weekly.output


def test_the_daily_run_hands_on_to_the_weekly_one_when_it_finishes(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    started: list[str] = []
    with_next_job(monkeypatch, started)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert started == ["projects/fantasy-helper-123456/locations/us-east4/jobs/weekly"]


def test_the_weekly_one_starts_even_when_a_league_failed_in_the_daily_run(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The overview is its own product and checks its own inputs; one league's failure in the
    daily run shouldn't also cost the day's overview. The run still exits 3 and pings fail."""
    snapshot_from(monkeypatch, tmp_path, today())

    def one_failed(*_a: object, **_k: object) -> DailyResult:
        report = IngestReport(leagues=[LeagueResult(league=league(), failure=FailureKind.AUTH)])
        return DailyResult(ingest=report)

    monkeypatch.setattr(cli_run, "run_daily", one_failed)
    started: list[str] = []
    with_next_job(monkeypatch, started)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert len(started) == 1
    assert h.beat.pings == ["start", "fail"]


def test_a_daily_run_that_stopped_never_starts_the_weekly_one(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_snapshot_yet(monkeypatch, tmp_path)  # the gate fails: this run never got going
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    started: list[str] = []
    with_next_job(monkeypatch, started)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert started == []  # nothing is built on an incomplete snapshot, and nothing chains from it


def test_a_dry_run_starts_nothing(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    started: list[str] = []
    with_next_job(monkeypatch, started)
    result = runner.invoke(app, ["run", "daily", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert started == []


def test_a_chain_that_cant_continue_alerts_and_fails_the_check(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(cli_run, "run_daily", clean_daily(h))
    with_next_job(monkeypatch, [], fails=True)
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == REPORTED_EXIT
    assert "couldn't start weekly: HTTP 403" in result.output
    assert h.beat.pings == ["start", "fail"]


def test_the_last_link_has_nothing_to_start(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot_from(monkeypatch, tmp_path, today())
    monkeypatch.setattr(
        cli_run, "run_weekly", lambda *_a, **_k: WeeklyResult(ingest=IngestReport())
    )
    started: list[str] = []
    monkeypatch.setattr(
        cli_run, "start_next",
        lambda settings, events, after: started.append(after),
    )  # fmt: skip
    result = runner.invoke(app, ["run", "weekly", "--require-snapshot-today"])
    assert result.exit_code == 0, result.output
    assert started == ["Weekly run"]  # called; with FH_NEXT_JOB unset it does nothing


def test_a_bad_secret_fails_the_check_at_once_not_an_hour_later(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reading the settings is inside the watchdog now: a missing or invalid secret pings start
    and fail immediately, so the alert comes at 06:18, not when the grace expires."""
    monkeypatch.setenv("FH_ENCRYPTION_KEY", "too-short")
    snapshot_from(monkeypatch, tmp_path, today())
    result = runner.invoke(app, ["run", "daily", "--require-snapshot-today"])
    assert result.exit_code == 1
    assert "FH_ENCRYPTION_KEY is invalid" in result.output
    assert h.beat.pings == ["start", "fail"]
