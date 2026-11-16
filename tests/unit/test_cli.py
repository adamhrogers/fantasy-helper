import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from fantasy_helper import __version__
from fantasy_helper.adapters import cloud_run
from fantasy_helper.cli import _runs
from fantasy_helper.cli import main as cli_main
from fantasy_helper.cli._common import REPORTED_EXIT, watched
from fantasy_helper.cli.doctor import supabase_region
from fantasy_helper.cli.main import app
from fantasy_helper.domain.runs import FailureKind, RunKind, RunRecord, RunStatus
from fantasy_helper.observability.healthchecks import HealthchecksHeartbeat
from fantasy_helper.pipeline.snapshot import DatasetResult, SnapshotResult, SnapshotSet
from fantasy_helper.storage import crypto, r2_store
from fantasy_helper.storage.raw_store import LocalRawStore, RawStoreError
from tests.fake_s3 import FakeS3, client_error, fake_store

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_keygen_prints_a_valid_key() -> None:
    result = runner.invoke(app, ["keygen"])
    assert result.exit_code == 0
    assert len(crypto.load_key(result.stdout.strip())) == crypto.KEY_BYTES


def test_keygen_topic_is_a_valid_ntfy_topic() -> None:
    result = runner.invoke(app, ["keygen", "--topic"])
    assert re.fullmatch(r"fh-[A-Za-z0-9_-]{20,61}", result.stdout.strip())


def test_doctor_with_nothing_configured_skips_and_passes() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "✓ config" in result.stdout
    for check in ("encryption", "database", "tracing", "alerts", "raw"):
        assert f"- {check}" in result.stdout


def test_doctor_passes_with_a_fresh_copy_of_env_example(tmp_path: Path) -> None:
    """Empty `KEY=` lines in .env.example mean "not configured", not an empty value."""
    shutil.copy(Path(__file__).parents[2] / ".env.example", tmp_path / ".env")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "- database" in result.stdout


def test_doctor_reports_a_malformed_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not a url")
    result = runner.invoke(app, ["doctor", "--only", "database"])
    assert result.exit_code == 1
    assert "bad DATABASE_URL" in result.stdout


def test_doctor_fails_on_a_bad_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_ENCRYPTION_KEY", "too-short")
    result = runner.invoke(app, ["doctor", "--only", "encryption"])
    assert result.exit_code == 1
    assert "✗ encryption" in result.stdout


def test_doctor_fails_when_the_database_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    result = runner.invoke(app, ["doctor", "--only", "database"])
    assert result.exit_code == 1
    assert "can't connect" in result.stdout


def test_doctor_fails_when_r2_is_chosen_but_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FH_RAW_STORE", "r2")
    result = runner.invoke(app, ["doctor", "--only", "raw"])
    assert result.exit_code == 1
    assert "FH_R2_ENDPOINT" in result.stdout


def test_snapshot_refuses_to_start_without_its_r2_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FH_RAW_STORE", "r2")
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert result.exit_code == REPORTED_EXIT  # alerted, so the workflow doesn't alert again
    assert "FH_R2_ENDPOINT" in result.output


def test_raw_upload_needs_r2(tmp_path: Path) -> None:
    (tmp_path / "data" / "raw").mkdir(parents=True)
    result = runner.invoke(app, ["raw", "upload"])
    assert result.exit_code == 1
    assert "FH_R2_ENDPOINT" in result.output


def test_raw_upload_needs_a_folder() -> None:
    result = runner.invoke(app, ["raw", "upload", "--from", "nowhere"])
    assert result.exit_code == 1
    assert "no raw folder" in result.output


@pytest.fixture
def r2_env(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    """R2 configured, with the bucket replaced by the in-memory stand-in."""
    monkeypatch.setenv("FH_R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("FH_R2_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("FH_R2_SECRET_ACCESS_KEY", "secret")
    s3 = FakeS3()
    monkeypatch.setattr(r2_store, "r2_store_from_settings", lambda _settings: fake_store(s3)[0])
    return s3


def test_doctor_passes_when_r2_refuses_overwrites(r2_env: FakeS3) -> None:
    result = runner.invoke(app, ["doctor", "--only", "raw"])
    assert result.exit_code == 0, result.stdout
    assert "refuses to overwrite" in result.stdout


def test_doctor_fails_on_mistyped_r2_keys(r2_env: FakeS3) -> None:
    r2_env.fail_next["list_objects_v2"] = client_error(
        "SignatureDoesNotMatch", 403, "ListObjectsV2"
    )
    result = runner.invoke(app, ["doctor", "--only", "raw"])
    assert result.exit_code == 1
    assert "secret access key doesn't match" in result.stdout


def test_doctor_fails_when_writes_are_denied(r2_env: FakeS3) -> None:
    r2_env.fail_next["put_object"] = client_error("AccessDenied", 403)
    result = runner.invoke(app, ["doctor", "--only", "raw"])
    assert result.exit_code == 1
    assert "AccessDenied" in result.stdout


def test_doctor_fails_when_r2_would_overwrite(r2_env: FakeS3) -> None:
    r2_env.enforce_if_none_match = False
    result = runner.invoke(app, ["doctor", "--only", "raw"])
    assert result.exit_code == 1
    assert "accepted an overwrite" in result.stdout


def _succeeded(*_args: object, **_kwargs: object) -> SnapshotResult:
    now = datetime.now(UTC)
    run = RunRecord(kind=RunKind.SNAPSHOT, status=RunStatus.SUCCEEDED, started_at=now, ended_at=now)
    return SnapshotResult(run_set=SnapshotSet.EVENING, run=run, results=(), recorded=False)


def test_a_snapshot_crash_is_alerted_and_exits_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*_args: object, **_kwargs: object) -> SnapshotResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_main, "run_snapshot", crash)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert result.exit_code == REPORTED_EXIT


def test_a_misconfigured_store_still_snapshots_then_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bucket path in FH_R2_ENDPOINT lets writes land under the wrong key while listing fails:
    the day is still saved, and the run exits as a reported failure."""

    class MisplacedStore(LocalRawStore):
        def check_access(self) -> None:
            raise RawStoreError("R2 list failed: NoSuchKey")

    ran: list[bool] = []

    def snapshot(*args: object, **kwargs: object) -> SnapshotResult:
        ran.append(True)
        return _succeeded()

    monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: MisplacedStore(tmp_path))
    monkeypatch.setattr(cli_main, "run_snapshot", snapshot)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert ran == [True]
    assert result.exit_code == REPORTED_EXIT
    assert "raw store check: R2 list failed: NoSuchKey" in result.output


def test_a_healthy_snapshot_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: LocalRawStore(tmp_path))
    monkeypatch.setattr(cli_main, "run_snapshot", _succeeded)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert result.exit_code == 0, result.output


def test_doctor_fails_when_prod_has_no_alert_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_ENV", "prod")
    result = runner.invoke(app, ["doctor", "--only", "alerts"])
    assert result.exit_code == 1
    assert "prod runs can't reach the phone" in result.stdout


class _Heartbeat:
    def __init__(self) -> None:
        self.pings: list[str] = []

    def start(self) -> None:
        self.pings.append("start")

    def success(self, detail: str = "") -> None:
        self.pings.append("success")

    def fail(self, detail: str = "") -> None:
        self.pings.append("fail")


@pytest.fixture
def heartbeat(monkeypatch: pytest.MonkeyPatch) -> tuple[_Heartbeat, list[str]]:
    beat, slugs = _Heartbeat(), []

    def make(_settings: object, slug: str) -> _Heartbeat:
        slugs.append(slug)
        return beat

    monkeypatch.setattr(cli_main, "heartbeat_from_settings", make)
    return beat, slugs


def test_a_healthy_snapshot_pings_start_then_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, heartbeat: tuple[_Heartbeat, list[str]]
) -> None:
    monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: LocalRawStore(tmp_path))
    monkeypatch.setattr(cli_main, "run_snapshot", _succeeded)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert result.exit_code == 0, result.output
    assert heartbeat[0].pings == ["start", "success"]
    assert heartbeat[1] == ["snapshot-evening"]


def test_a_reported_failure_still_pings_fail(
    monkeypatch: pytest.MonkeyPatch, heartbeat: tuple[_Heartbeat, list[str]]
) -> None:
    """The phone alert may never have been delivered (run events swallow send errors), so the
    watchdog raises its own alert for any exit but a clean one."""

    def crash(*_args: object, **_kwargs: object) -> SnapshotResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_main, "run_snapshot", crash)
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == REPORTED_EXIT
    assert heartbeat[0].pings == ["start", "fail"]
    assert heartbeat[1] == ["snapshot-morning"]


def test_a_failure_the_app_could_not_report_pings_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, heartbeat: tuple[_Heartbeat, list[str]]
) -> None:
    def interrupted(*_args: object, **_kwargs: object) -> SnapshotResult:
        raise KeyboardInterrupt  # stands in for anything that escapes the app's own alerting

    monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: LocalRawStore(tmp_path))
    monkeypatch.setattr(cli_main, "run_snapshot", interrupted)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert result.exit_code != 0
    assert heartbeat[0].pings == ["start", "fail"]


@pytest.mark.parametrize(
    ("exit_code", "pings"),
    [(0, ["start", "success"]), (1, ["start", "fail"]), (REPORTED_EXIT, ["start", "fail"])],
)
def test_watched_maps_every_exit(exit_code: int, pings: list[str]) -> None:
    beat = _Heartbeat()
    with pytest.raises(typer.Exit), watched(beat):
        raise typer.Exit(code=exit_code)
    assert beat.pings == pings


def test_a_broken_ping_key_never_stops_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key httpx can't put in a URL raises InvalidURL, not an HTTPError: the run goes on."""
    ran: list[bool] = []

    def snapshot(*args: object, **kwargs: object) -> SnapshotResult:
        ran.append(True)
        return _succeeded()

    def broken(_settings: object, slug: str) -> HealthchecksHeartbeat:
        client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
        return HealthchecksHeartbeat(
            server="https://hc.example", ping_key="bad\x00key", slug=slug, client=client
        )

    monkeypatch.setattr(cli_main, "heartbeat_from_settings", broken)
    monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: LocalRawStore(tmp_path))
    monkeypatch.setattr(cli_main, "run_snapshot", snapshot)
    result = runner.invoke(app, ["snapshot", "--set", "evening"])
    assert ran == [True]
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("url", "region"),
    [
        (
            "postgresql://postgres.abc:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
            "us-east-1",
        ),
        (
            "postgresql://postgres.abc:pw@aws-1-eu-west-2.pooler.supabase.com:6543/postgres",
            "eu-west-2",
        ),
        (
            "postgresql://u@db.abc.supabase.co:5432/postgres",
            None,
        ),  # the direct host names no region
        ("postgresql://fh@localhost:54329/fh_test", None),
    ],
)
def test_the_doctor_names_the_supabase_region_from_the_pooler_host(
    url: str, region: str | None
) -> None:
    assert supabase_region(url) == region


# ---- the morning chain: the snapshot hands on only when everything saved (ADR 0028)

NEXT = "projects/fantasy-helper-123456/locations/us-east4/jobs/daily"


def _morning(*, failed: int = 0, run_set: SnapshotSet = SnapshotSet.MORNING) -> SnapshotResult:
    now = datetime.now(UTC)
    ok = DatasetResult("espn.players_projections", "k", 10, 100, now, None)
    bad = DatasetResult("sleeper.players", None, 0, 0, now, "HTTP 500", FailureKind.SERVER_ERROR)
    results = (ok, *([bad] * failed))
    status = RunStatus.SUCCEEDED if not failed else RunStatus.DEGRADED
    run = RunRecord(kind=RunKind.SNAPSHOT, status=status, started_at=now, ended_at=now)
    return SnapshotResult(run_set=run_set, run=run, results=results, recorded=False)


class _Chain:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fails: bool = False):
        self.store = LocalRawStore(tmp_path)
        self.started: list[str] = []
        monkeypatch.setattr(cli_main, "raw_store_from_settings", lambda _s: self.store)

        def starter(resource: str) -> None:
            if fails:
                raise cloud_run.CloudRunError("HTTP 503")
            self.started.append(resource)

        real = _runs.start_next  # the real one, with the Google call swapped for `starter`
        monkeypatch.setattr(
            cli_main, "start_next",
            lambda settings, events, after: real(settings, events, after=after, starter=starter),
        )  # fmt: skip

    @property
    def markers(self) -> int:
        return len(self.store.keys("fh", "snapshot_complete"))


def test_a_complete_morning_snapshot_is_marked_and_starts_the_daily_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _Chain(monkeypatch, tmp_path)
    monkeypatch.setenv("FH_NEXT_JOB", NEXT)
    monkeypatch.setattr(cli_main, "run_snapshot", lambda *_a, **_k: _morning())
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == 0, result.output
    assert chain.markers == 1
    assert chain.started == [NEXT]
    assert "the morning set is complete" in result.output


def test_on_the_laptop_a_complete_snapshot_is_marked_but_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _Chain(monkeypatch, tmp_path)  # FH_NEXT_JOB isn't set
    monkeypatch.setattr(cli_main, "run_snapshot", lambda *_a, **_k: _morning())
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == 0, result.output
    assert (chain.markers, chain.started) == (1, [])


def test_an_incomplete_morning_snapshot_starts_nothing_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One dataset failed: no marker, so the daily run can't start from it, and the daily
    run's late-report check tells the phone the report isn't coming."""
    chain = _Chain(monkeypatch, tmp_path)
    monkeypatch.setenv("FH_NEXT_JOB", NEXT)
    monkeypatch.setattr(cli_main, "run_snapshot", lambda *_a, **_k: _morning(failed=1))
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == REPORTED_EXIT  # the snapshot itself is degraded
    assert (chain.markers, chain.started) == (0, [])
    assert "the morning set is incomplete (1 of 2 datasets failed)" in result.output


@pytest.mark.parametrize(
    "args",
    [["--set", "morning", "--only", "espn.game_logs"], ["--set", "evening"]],
    ids=["a partial backfill", "the evening set"],
)
def test_only_a_whole_morning_set_completes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    chain = _Chain(monkeypatch, tmp_path)
    monkeypatch.setenv("FH_NEXT_JOB", NEXT)
    monkeypatch.setattr(
        cli_main, "run_snapshot",
        lambda *_a, **_k: _morning(run_set=SnapshotSet(args[1])),
    )  # fmt: skip
    result = runner.invoke(app, ["snapshot", *args])
    assert result.exit_code == 0, result.output
    assert (chain.markers, chain.started) == (0, [])


def test_a_chain_that_cant_continue_alerts_after_the_data_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _Chain(monkeypatch, tmp_path, fails=True)
    monkeypatch.setenv("FH_NEXT_JOB", NEXT)
    monkeypatch.setattr(cli_main, "run_snapshot", lambda *_a, **_k: _morning())
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == REPORTED_EXIT
    assert chain.markers == 1  # everything was saved and marked; only the hand-over broke
    assert "couldn't start daily: HTTP 503" in result.output


def test_a_marker_that_cant_be_written_stops_the_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _Chain(monkeypatch, tmp_path)
    monkeypatch.setenv("FH_NEXT_JOB", NEXT)
    monkeypatch.setattr(cli_main, "run_snapshot", lambda *_a, **_k: _morning())

    def refuse(*_a: object, **_k: object) -> str:
        raise RawStoreError("R2 refused the write")

    monkeypatch.setattr(chain.store, "put", refuse)
    result = runner.invoke(app, ["snapshot", "--set", "morning"])
    assert result.exit_code == REPORTED_EXIT
    assert chain.started == []
    assert "completion marker not written" in result.output
