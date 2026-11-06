from pathlib import Path

import pytest

from fantasy_helper.config import Settings


def test_defaults_when_nothing_is_configured() -> None:
    settings = Settings()
    assert settings.env == "dev"
    assert settings.database_url is None
    assert settings.encryption_key is None
    assert settings.langsmith_tracing is False


def test_reads_a_local_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://u:p@localhost/db\nFH_ENV=prod\n")
    settings = Settings()
    assert settings.env == "prod"
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == "postgresql://u:p@localhost/db"


def test_secrets_never_appear_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FH_ANTHROPIC_API_KEY", "sk-very-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2@localhost/db")
    rendered = repr(Settings())
    assert "sk-very-secret" not in rendered
    assert "hunter2" not in rendered


def test_app_ignores_the_claude_code_api_key_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's key is FH_ANTHROPIC_API_KEY, so it can't come from the Claude Code shell."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-shell")
    assert Settings().anthropic_api_key is None
