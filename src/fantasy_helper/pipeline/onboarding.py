"""Adding a user's leagues: the CLI's `fh league add` now, the phone app's onboarding in M7.

Each league is verified against its platform before anything is saved, and ESPN cookies are
stored (encrypted) only after ESPN has accepted them.
"""

import json
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from fantasy_helper.adapters.espn import league as espn_league
from fantasy_helper.adapters.sleeper import league as sleeper_league
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.users import EspnCredentials, Role, User
from fantasy_helper.storage.repositories import credentials, leagues, users


class MissingCredentialsError(Exception):
    pass


@dataclass(frozen=True)
class AddedLeague:
    league: League
    created: bool
    team_count: int
    my_team_label: str | None  # the user's team, as the platform names it; None if not found


def ensure_user(
    session: Session, *, email: str, display_name: str, timezone: str, role: Role = Role.OWNER
) -> User:
    email = email.strip().lower()
    existing = users.get_by_email(session, email)
    if existing:
        return existing
    return users.create(
        session, User(email=email, display_name=display_name, timezone=timezone, role=role)
    )


def add_espn_league(
    session: Session,
    client: httpx.Client,
    *,
    user: User,
    league_id: str,
    season: int,
    key: bytes,
    new_credentials: EspnCredentials | None = None,
) -> AddedLeague:
    """Verify access to an ESPN league and save it.

    Uses `new_credentials` if given (saving them once ESPN accepts them), otherwise the
    user's stored cookies. Raises EspnAccessError or MissingCredentialsError.
    """
    creds = new_credentials or credentials.load_espn(session, user.id, key)
    if creds is None:
        raise MissingCredentialsError("no ESPN cookies saved for this user yet")
    response = espn_league.fetch_league(client, season, league_id, creds)
    summary = espn_league.summarize(
        json.loads(response.body), season, creds.swid.get_secret_value()
    )
    if new_credentials is not None:
        credentials.save_espn(session, user.id, new_credentials, key)
    league, created = leagues.upsert(
        session,
        League(
            user_id=user.id,
            platform=Platform.ESPN,
            external_id=league_id,
            season=season,
            name=summary.name,
            my_team_external_id=summary.my_team_id,
        ),
    )
    return AddedLeague(league, created, summary.team_count, summary.my_team_name)


def add_sleeper_league(
    session: Session, client: httpx.Client, *, user: User, league_id: str, username: str | None
) -> AddedLeague:
    """Verify a Sleeper league exists and save it. Raises SleeperLookupError."""
    user_id = None
    if username:
        user_id = str(json.loads(sleeper_league.fetch_user(client, username).body)["user_id"])
    league_data = json.loads(sleeper_league.fetch_league(client, league_id).body)
    rosters = json.loads(sleeper_league.fetch_rosters(client, league_id).body)
    summary = sleeper_league.summarize(league_data, rosters, user_id)
    league, created = leagues.upsert(
        session,
        League(
            user_id=user.id,
            platform=Platform.SLEEPER,
            external_id=league_id,
            season=summary.season,
            name=summary.name,
            my_team_external_id=summary.my_roster_id,
        ),
    )
    label = f"roster {summary.my_roster_id}" if summary.my_roster_id else None
    return AddedLeague(league, created, summary.team_count, label)
