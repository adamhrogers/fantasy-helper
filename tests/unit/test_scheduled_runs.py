"""The scheduled runs (`fh run daily|weekly`, `fh ingest`): they wait for this morning's snapshot,
take turns, ping the watchdog, and exit non-zero for anything but a clean run (ADR 0025, 0026)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fantasy_helper.cli import ingest as cli_ingest
from fantasy_helper.cli import run as cli_run
from fantasy_helper.cli._common import REPORTED_EXIT
from fantasy_helper.cli.main import app
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.runs import FailureKind, RunRecord, RunStatus
from fantasy_helper.domain.users import Role, User
from fantasy_helper.pipeline.daily import DailyResult
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
    store = LocalRawStore(tmp_path / "raw")
    store.put(source="espn", dataset="game_logs", fetched_at=at, body=b"{}", meta={})
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
        store.put(source="espn", dataset="game_logs", fetched_at=today(), body=b"{}", meta={})

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
