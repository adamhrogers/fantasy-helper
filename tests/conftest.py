from collections.abc import Iterator
from pathlib import Path

import pytest

from fantasy_helper.config import Settings, get_settings

_APP_ENV_VARS = sorted(
    {field.alias for field in Settings.model_fields.values() if field.alias is not None}
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Tests never see your real .env or any secret exported in your shell."""
    for name in _APP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # so Settings can't find the repo's .env
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
