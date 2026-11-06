"""System settings and secrets. Never user or league data (docs/architecture.md §12.1)."""

from fantasy_helper.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
