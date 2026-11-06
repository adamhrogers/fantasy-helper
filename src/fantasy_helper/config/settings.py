"""Typed system settings, read from the environment or a local, git-ignored `.env` file.

Only the system's own secrets and switches live here. League IDs, platform credentials and
anything per user live in the database, with credentials encrypted (docs/architecture.md §12.1).

Values in `.env` are read by this process only and are never exported to your shell, so the
Claude API key cannot leak into the shell you run Claude Code in.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # `KEY=` (as in .env.example) means unset, not ""
        # Env vars are matched by alias only. Matching by field name too would let the
        # shell's ANTHROPIC_API_KEY fill `anthropic_api_key` (tests/unit/test_settings.py).
    )

    env: Literal["dev", "test", "prod"] = Field(default="dev", alias="FH_ENV")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", alias="FH_LOG_LEVEL"
    )
    log_json: bool = Field(default=False, alias="FH_LOG_JSON")

    database_url: SecretStr | None = Field(default=None, alias="DATABASE_URL")
    encryption_key: SecretStr | None = Field(default=None, alias="FH_ENCRYPTION_KEY")

    langsmith_api_key: SecretStr | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_project: str = Field(default="fantasy-helper", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str | None = Field(default=None, alias="LANGSMITH_ENDPOINT")

    ntfy_topic: SecretStr | None = Field(default=None, alias="FH_NTFY_TOPIC")
    ntfy_server: str = Field(default="https://ntfy.sh", alias="FH_NTFY_SERVER")

    # Used from M4. Deliberately not ANTHROPIC_API_KEY (spec: Cost and tokens).
    anthropic_api_key: SecretStr | None = Field(default=None, alias="FH_ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
