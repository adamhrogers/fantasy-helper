"""A Sleeper league. Public: no login, just the league ID (and a username to find your team)."""

from dataclasses import dataclass
from typing import Any

import httpx

from fantasy_helper.adapters._http import RawResponse, get_raw
from fantasy_helper.adapters.sleeper.public import API, SOURCE


class SleeperLookupError(Exception):
    pass


@dataclass(frozen=True)
class SleeperLeagueSummary:
    name: str
    season: int  # the year the season ends, like the rest of the domain
    team_count: int
    my_roster_id: str | None


def _get(client: httpx.Client, dataset: str, path: str) -> RawResponse:
    response = get_raw(client, source=SOURCE, dataset=dataset, url=f"{API}{path}")
    # Sleeper answers unknown IDs with 200 and a JSON null.
    if response.status_code == 404 or (response.ok and response.body.strip() == b"null"):
        raise SleeperLookupError(f"nothing found at {path}")
    if not response.ok:
        raise SleeperLookupError(f"Sleeper returned HTTP {response.status_code} for {path}")
    return response


def fetch_user(client: httpx.Client, username: str) -> RawResponse:
    return _get(client, "user", f"/user/{username}")


def fetch_league(client: httpx.Client, league_id: str) -> RawResponse:
    return _get(client, "league", f"/league/{league_id}")


def fetch_rosters(client: httpx.Client, league_id: str) -> RawResponse:
    return _get(client, "rosters", f"/league/{league_id}/rosters")


def fetch_users(client: httpx.Client, league_id: str) -> RawResponse:
    """League members. Display names and team names are personal: redact before recording."""
    return _get(client, "users", f"/league/{league_id}/users")


def fetch_matchups(client: httpx.Client, league_id: str, week: int) -> RawResponse:
    """One week's matchups with points per player. No lock-in data (M1 spike, Q4)."""
    return _get(client, f"matchups_{week}", f"/league/{league_id}/matchups/{week}")


def fetch_transactions(client: httpx.Client, league_id: str, week: int) -> RawResponse:
    return _get(client, f"transactions_{week}", f"/league/{league_id}/transactions/{week}")


def summarize(league: Any, rosters: Any, user_id: str | None) -> SleeperLeagueSummary:
    if league.get("sport") not in (None, "nba"):
        raise SleeperLookupError(f"league is a {league.get('sport')} league, not NBA")
    mine = next((r for r in rosters if user_id and r.get("owner_id") == user_id), None)
    return SleeperLeagueSummary(
        name=str(league.get("name") or f"Sleeper league {league.get('league_id')}"),
        season=int(league["season"]) + 1,  # Sleeper labels 2026-27 as "2026"
        team_count=int(league.get("total_rosters") or len(rosters)),
        my_roster_id=str(mine["roster_id"]) if mine else None,
    )
