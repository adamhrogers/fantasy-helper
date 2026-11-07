"""A private ESPN league, read with the owner's login cookies (espn_s2 and SWID).

ESPN identifies the logged-in member by SWID, which also appears in each team's `owners`,
so the user's own team can be found without asking.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from fantasy_helper.adapters._http import QueryParams, RawResponse, get_raw
from fantasy_helper.adapters.espn.public import BASE, SOURCE
from fantasy_helper.domain.league import ScoringType
from fantasy_helper.domain.users import EspnCredentials

# ESPN's scoringType values (settings.scoringSettings.scoringType).
_SCORING_TYPES = {
    "H2H_POINTS": ScoringType.POINTS,
    "H2H_CATEGORY": ScoringType.CATEGORIES,
    "H2H_MOST_CATEGORIES": ScoringType.CATEGORIES,
    "ROTO": ScoringType.CATEGORIES,
}


class EspnAccessError(Exception):
    """ESPN refused the request: the cookies are wrong or expired, or the league doesn't exist."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EspnLeagueSummary:
    name: str
    season: int
    team_count: int
    scoring_type: ScoringType | None
    my_team_id: str | None
    my_team_name: str | None


def cookie_header(credentials: EspnCredentials) -> dict[str, str]:
    return {
        "Cookie": (
            f"espn_s2={credentials.espn_s2.get_secret_value()}; "
            f"SWID={credentials.swid.get_secret_value()}"
        )
    }


# Transaction types worth keeping: real roster changes and every stage of a trade.
TRANSACTION_TYPES = (
    "FREEAGENT", "WAIVER", "WAIVER_ERROR",
    "TRADE_PROPOSAL", "TRADE_ACCEPT", "TRADE_DECLINE", "TRADE_UPHOLD", "TRADE_VETO",
)  # fmt: skip


def _league_get(
    client: httpx.Client,
    season: int,
    league_id: str,
    credentials: EspnCredentials,
    *,
    dataset: str,
    params: QueryParams,
    headers: Mapping[str, str] | None = None,
) -> RawResponse:
    response = get_raw(
        client,
        source=SOURCE,
        dataset=dataset,
        url=BASE.format(season=season) + f"/segments/0/leagues/{league_id}",
        params=params,
        headers=headers,
        secret_headers=cookie_header(credentials),
    )
    if response.status_code in (401, 403):
        raise EspnAccessError(
            "ESPN refused the cookies (wrong, expired, or not in this league)",
            response.status_code,
        )
    if response.status_code == 404:
        raise EspnAccessError(
            f"no ESPN league {league_id} for the {season - 1}-{season % 100} season", 404
        )
    if not response.ok:
        raise EspnAccessError(f"ESPN returned HTTP {response.status_code}", response.status_code)
    return response


def fetch_league(
    client: httpx.Client,
    season: int,
    league_id: str,
    credentials: EspnCredentials,
    views: tuple[str, ...] = ("mSettings", "mTeam"),
) -> RawResponse:
    """Settings and teams: enough to verify access and find the user's team."""
    return _league_get(
        client, season, league_id, credentials, dataset="league", params={"view": list(views)}
    )


def fetch_settings(
    client: httpx.Client, season: int, league_id: str, credentials: EspnCredentials
) -> RawResponse:
    """Scoring weights, roster slots, add limits, schedule settings and the current period."""
    return _league_get(
        client, season, league_id, credentials,
        dataset="settings", params={"view": ["mSettings", "mStatus"]},
    )  # fmt: skip


def fetch_teams_rosters(
    client: httpx.Client, season: int, league_id: str, credentials: EspnCredentials
) -> RawResponse:
    return _league_get(
        client, season, league_id, credentials,
        dataset="teams_rosters", params={"view": ["mTeam", "mRoster"]},
    )  # fmt: skip


def fetch_lineups(
    client: httpx.Client,
    season: int,
    league_id: str,
    credentials: EspnCredentials,
    scoring_period: int,
) -> RawResponse:
    """Every team's lineup on one scoring day, past days included (view mRoster with a
    scoringPeriodId). Used for who was in a starting slot that day; the points in it are never
    used (ADR 0022)."""
    return _league_get(
        client, season, league_id, credentials,
        dataset=f"lineups/{scoring_period}",
        params={"view": "mRoster", "scoringPeriodId": str(scoring_period)},
    )  # fmt: skip


def fetch_matchups(
    client: httpx.Client, season: int, league_id: str, credentials: EspnCredentials
) -> RawResponse:
    """Every matchup of the season, with points per scoring period (day)."""
    return _league_get(
        client, season, league_id, credentials,
        dataset="matchups", params={"view": ["mMatchupScore", "mScoreboard"]},
    )  # fmt: skip


def fetch_transactions(
    client: httpx.Client,
    season: int,
    league_id: str,
    credentials: EspnCredentials,
    scoring_period: int,
) -> RawResponse:
    """One day's transactions. The unfiltered view returns only recent lineup moves, so the
    full history is read one scoring period at a time with a type filter (M1 spike)."""
    return _league_get(
        client, season, league_id, credentials,
        dataset="transactions",
        params={"view": "mTransactions2", "scoringPeriodId": str(scoring_period)},
        headers={"x-fantasy-filter": json.dumps(
            {"transactions": {"filterType": {"value": list(TRANSACTION_TYPES)}}},
            separators=(",", ":"),
        )},
    )  # fmt: skip


def summarize(data: Any, season: int, swid: str) -> EspnLeagueSummary:
    settings = data.get("settings", {})
    scoring = settings.get("scoringSettings", {}).get("scoringType")
    my_team = next(
        (t for t in data.get("teams", []) if _same_swid(swid, t.get("owners", []))), None
    )
    return EspnLeagueSummary(
        name=settings.get("name") or f"ESPN league {data.get('id', '')}".strip(),
        season=season,
        team_count=int(settings.get("size") or len(data.get("teams", []))),
        scoring_type=_SCORING_TYPES.get(scoring),
        my_team_id=str(my_team["id"]) if my_team else None,
        my_team_name=_team_name(my_team) if my_team else None,
    )


def _same_swid(swid: str, owners: list[Any]) -> bool:
    wanted = swid.strip().strip("{}").lower()
    return any(str(o).strip().strip("{}").lower() == wanted for o in owners)


def _team_name(team: dict[str, Any]) -> str:
    if team.get("name"):
        return str(team["name"])
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip() or str(team["id"])
