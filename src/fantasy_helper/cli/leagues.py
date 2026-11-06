"""`fh league add | list`: register leagues and their logins in the database (ADR 0007).

League IDs and cookies never go in `.env`. ESPN cookies are typed at a hidden prompt, checked
against ESPN, then stored encrypted.
"""

from enum import StrEnum
from typing import Annotated

import typer
from pydantic import SecretStr
from sqlalchemy.orm import Session

from fantasy_helper.adapters._http import make_client
from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.sleeper.league import SleeperLookupError
from fantasy_helper.cli._common import fail as _fail
from fantasy_helper.cli._common import require_db_and_key as _require
from fantasy_helper.cli._common import transaction as _transaction
from fantasy_helper.config import get_settings
from fantasy_helper.domain.calendar import season_end_year
from fantasy_helper.domain.league import Platform
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.domain.users import EspnCredentials, User
from fantasy_helper.pipeline import onboarding
from fantasy_helper.storage.repositories import credentials, leagues, users

app = typer.Typer(help="Add and list your fantasy leagues.", no_args_is_help=True)

COOKIE_HELP = "docs/runbooks/adding-leagues.md"


class PlatformChoice(StrEnum):
    ESPN = "espn"
    SLEEPER = "sleeper"


def _resolve_user(session: Session, email: str | None) -> User:
    if email:
        found = users.get_by_email(session, email)
        if found is None:
            raise _fail(f"no user with email {email}")
        return found
    owner = users.get_owner(session)
    if owner:
        return owner
    typer.echo("No user yet. Let's create yours (one time).")
    return onboarding.ensure_user(
        session,
        email=typer.prompt("  Email"),
        display_name=typer.prompt("  Name"),
        timezone=typer.prompt("  Time zone", default="America/New_York"),
    )


def _prompt_espn_cookies() -> EspnCredentials:
    typer.echo(f"ESPN login cookies (input is hidden). Where to find them: {COOKIE_HELP}")
    espn_s2 = typer.prompt("  espn_s2", hide_input=True).strip()
    swid = typer.prompt("  SWID", hide_input=True).strip()
    return EspnCredentials(espn_s2=SecretStr(espn_s2), swid=SecretStr(swid))


@app.command("add")
def add(
    platform: Annotated[PlatformChoice, typer.Argument(help="espn or sleeper")],
    league_id: Annotated[str, typer.Argument(help="The league's ID from its URL")],
    season: Annotated[
        int | None, typer.Option(help="Year the season ends (2027 for 2026-27). Default: now.")
    ] = None,
    user_email: Annotated[
        str | None, typer.Option("--user", help="Add for this user (default: the owner)")
    ] = None,
    username: Annotated[
        str | None, typer.Option(help="Sleeper username, to find your team")
    ] = None,
    new_cookies: Annotated[
        bool, typer.Option("--new-cookies", help="Replace the saved ESPN cookies")
    ] = False,
) -> None:
    """Verify a league with its platform and save it (and, for ESPN, your encrypted cookies)."""
    url, key = _require(get_settings())
    season = season or season_end_year(SystemClock().now().date())
    try:
        with _transaction(url) as session, make_client() as client:
            user = _resolve_user(session, user_email)
            if platform is PlatformChoice.ESPN:
                fresh = None
                if new_cookies or not credentials.has(session, user.id, Platform.ESPN):
                    fresh = _prompt_espn_cookies()
                added = onboarding.add_espn_league(
                    session,
                    client,
                    user=user,
                    league_id=league_id,
                    season=season,
                    key=key,
                    new_credentials=fresh,
                )
            else:
                if username is None:
                    username = (
                        typer.prompt(
                            "Your Sleeper username (to find your team; Enter to skip)",
                            default="",
                            show_default=False,
                        ).strip()
                        or None
                    )
                added = onboarding.add_sleeper_league(
                    session, client, user=user, league_id=league_id, username=username
                )
    except (EspnAccessError, SleeperLookupError, onboarding.MissingCredentialsError) as exc:
        raise _fail(str(exc)) from exc

    league = added.league
    verb = "Added" if added.created else "Updated"
    team = added.my_team_label or "not found (check the cookies' account or the username)"
    typer.echo(
        f"✓ {verb} {league.platform.value} league {league.name!r} ({league.season - 1}-"
        f"{league.season % 100}, {added.team_count} teams). Your team: {team}"
    )
    if platform is PlatformChoice.ESPN:
        typer.echo("  ESPN cookies are stored encrypted in the database.")


@app.command("list")
def list_leagues(
    user_email: Annotated[str | None, typer.Option("--user")] = None,
) -> None:
    """List the leagues saved for a user."""
    url, _ = _require(get_settings())
    with _transaction(url) as session:
        user = users.get_by_email(session, user_email) if user_email else users.get_owner(session)
        if user is None:
            raise _fail("no user yet: run `fh league add` first")
        rows = leagues.list_for_user(session, user.id)
    if not rows:
        typer.echo("No leagues yet.")
    for league in rows:
        team = league.my_team_external_id or "?"
        typer.echo(
            f"{league.platform.value:<8} {league.external_id:<20} {league.season} "
            f"team {team:<6} {league.name}"
        )
