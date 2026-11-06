"""ESPN Fantasy Basketball endpoints that need no login.

Endpoint and view names follow the community `espn-api` library. Findings from the M1 spike
(docs/spikes/m1-data-sources.md): the public player view carries per-stat **season**
projections (totals and per-game averages) and injury status, but no per-day projections.
"""

import json
from typing import Any

import httpx

from fantasy_helper.adapters._http import RawResponse, get_raw

SOURCE = "espn"
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}"

MIN_PLAYERS = 300  # fewer than this in a full pull means the API changed or failed quietly
# ESPN projects only fantasy-relevant players: about 350 of 1,100 active on 2026-11-06.
# Deep-bench players still matter for their injury status and ownership.
MIN_PROJECTED = 250


def season_info(client: httpx.Client, season: int) -> RawResponse:
    """Season metadata, including the current scoring period (ESPN counts days)."""
    return get_raw(client, source=SOURCE, dataset="season", url=BASE.format(season=season))


def players_projections(client: httpx.Client, season: int, *, limit: int = 1200) -> RawResponse:
    """Players by ownership with season projections, current-season totals and injury status.

    The stats filter keeps only this season's actual and projected totals plus the latest
    game, which cuts the payload about tenfold. Game logs are fetched separately when needed:
    they never change, so they don't need daily snapshots.
    """
    player_filter = {
        "players": {
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "filterStatsForTopScoringPeriodIds": {
                "value": 1,
                "additionalValue": [f"00{season}", f"10{season}"],
            },
        }
    }
    return get_raw(
        client,
        source=SOURCE,
        dataset="players_projections",
        url=BASE.format(season=season) + "/segments/0/leaguedefaults/3",
        params={"view": "kona_player_info"},
        headers={"x-fantasy-filter": json.dumps(player_filter, separators=(",", ":"))},
    )


def pro_schedule(client: httpx.Client, season: int) -> RawResponse:
    """Every NBA team's schedule (game dates and IDs), for games per week."""
    return get_raw(
        client,
        source=SOURCE,
        dataset="pro_schedule",
        url=BASE.format(season=season),
        params={"view": "proTeamSchedules_wl"},
    )


ATHLETES = "https://sports.core.api.espn.com/v3/sports/basketball/nba/athletes"
MIN_ATHLETES = 400


def athletes(client: httpx.Client) -> RawResponse:
    """ESPN's public sports API (not Fantasy): every NBA athlete with a date of birth, under the
    same IDs ESPN Fantasy uses. Birth dates make identity matching trade-proof (ADR 0018)."""
    return get_raw(
        client,
        source=SOURCE,
        dataset="athletes",
        url=ATHLETES,
        params={"limit": "20000", "active": "true"},
    )


def validate_athletes(data: Any) -> str | None:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) < MIN_ATHLETES:
        return f"only {len(items) if isinstance(items, list) else 0} athletes"
    if sum(1 for i in items if i.get("dateOfBirth")) < len(items) // 2:
        return "most athletes have no date of birth"
    return None


def validate_season(data: Any) -> str | None:
    if not isinstance(data, dict) or "currentScoringPeriod" not in data:
        return "season info is missing currentScoringPeriod"
    return None


def validate_players(data: Any) -> str | None:
    players = data.get("players") if isinstance(data, dict) else None
    if not isinstance(players, list):
        return "no players list"
    if len(players) < MIN_PLAYERS:
        return f"only {len(players)} players (expected at least {MIN_PLAYERS})"
    projected = sum(
        1
        for entry in players
        if any(s.get("statSourceId") == 1 for s in entry.get("player", {}).get("stats", []))
    )
    if projected < MIN_PROJECTED:
        return f"only {projected} players have projections (expected at least {MIN_PROJECTED})"
    return None


def validate_schedule(data: Any) -> str | None:
    teams = data.get("settings", {}).get("proTeams") if isinstance(data, dict) else None
    if not isinstance(teams, list) or len(teams) < 30:
        return "pro team schedule has fewer than 30 teams"
    return None


def item_count(data: Any) -> int:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return len(data["items"])
    if isinstance(data, dict) and isinstance(data.get("players"), list):
        return len(data["players"])
    if isinstance(data, dict) and isinstance(data.get("settings", {}).get("proTeams"), list):
        return len(data["settings"]["proTeams"])
    return 1
