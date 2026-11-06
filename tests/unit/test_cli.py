import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fantasy_helper import __version__
from fantasy_helper.cli.main import app
from fantasy_helper.storage import crypto

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
    for check in ("encryption", "database", "tracing", "alerts"):
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
