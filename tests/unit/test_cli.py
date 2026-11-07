import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantasy_helper import __version__
from fantasy_helper.cli import main as cli_main
from fantasy_helper.cli._common import REPORTED_EXIT
from fantasy_helper.cli.main import app
from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.pipeline.cron import CRON_ENTRIES
from fantasy_helper.pipeline.snapshot import SnapshotResult, SnapshotSet
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


def test_snapshot_from_the_other_offsets_cron_entry_skips_quietly() -> None:
    offset = datetime.now(UTC).astimezone(NBA_TIMEZONE).utcoffset()
    cron = next(c for c, e in CRON_ENTRIES.items() if timedelta(hours=e.utc_offset_hours) != offset)
    result = runner.invoke(app, ["snapshot", "--cron", cron])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.stdout


def test_snapshot_rejects_an_unknown_cron_entry() -> None:
    result = runner.invoke(app, ["snapshot", "--cron", "0 9 * * *"])
    assert result.exit_code == 2
    assert "pipeline/cron.py" in result.output


def test_snapshot_takes_set_or_cron_not_both() -> None:
    result = runner.invoke(app, ["snapshot", "--set", "evening", "--cron", "17 11 * * *"])
    assert result.exit_code == 2


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
