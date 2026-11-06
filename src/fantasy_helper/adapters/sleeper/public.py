"""Sleeper endpoints that need no login.

`/v1/...` is Sleeper's documented API. The projections endpoints on api.sleeper.com are
undocumented (used by community libraries); the M1 spike confirmed they cover the NBA with
per-stat season projections and per-game weekly projections.
Sleeper asks that the full player list be fetched at most once a day.
"""

from typing import Any

import httpx

from fantasy_helper.adapters._http import RawResponse, get_raw

SOURCE = "sleeper"
API = "https://api.sleeper.app/v1"
PROJECTIONS = "https://api.sleeper.com/projections/nba"

MIN_PLAYERS = 1000
MIN_PROJECTIONS = 300


def state(client: httpx.Client) -> RawResponse:
    """Current season label ("2026" for 2026-27), season type and week."""
    return get_raw(client, source=SOURCE, dataset="state", url=f"{API}/state/nba")


def players(client: httpx.Client) -> RawResponse:
    """Every NBA player with injury status and cross-platform IDs (espn_id, yahoo_id, …)."""
    return get_raw(client, source=SOURCE, dataset="players", url=f"{API}/players/nba")


def season_projections(client: httpx.Client, season_label: str) -> RawResponse:
    return get_raw(
        client,
        source=SOURCE,
        dataset="projections_season",
        url=f"{PROJECTIONS}/{season_label}",
        params={"season_type": "regular"},
    )


def week_projections(client: httpx.Client, season_label: str, week: int) -> RawResponse:
    """Per-game projections for every game in a Sleeper week."""
    return get_raw(
        client,
        source=SOURCE,
        dataset="projections_week",
        url=f"{PROJECTIONS}/{season_label}/{week}",
        params={"season_type": "regular"},
    )


def validate_state(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("season") or "week" not in data:
        return "state is missing season or week"
    return None


def validate_players(data: Any) -> str | None:
    if not isinstance(data, dict) or len(data) < MIN_PLAYERS:
        size = len(data) if isinstance(data, dict) else 0
        return f"only {size} players (expected at least {MIN_PLAYERS})"
    return None


def validate_projections(data: Any) -> str | None:
    if not isinstance(data, list) or len(data) < MIN_PROJECTIONS:
        size = len(data) if isinstance(data, list) else 0
        return f"only {size} projection rows (expected at least {MIN_PROJECTIONS})"
    with_stats = sum(1 for row in data if isinstance(row, dict) and row.get("stats"))
    if with_stats < len(data) // 2:
        return f"only {with_stats} of {len(data)} projection rows have stats"
    return None


def item_count(data: Any) -> int:
    if isinstance(data, list | dict):
        return len(data)
    return 1
