import contextlib
import uuid
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from fantasy_helper.adapters import _http
from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.cli import leagues as leagues_cli
from fantasy_helper.cli.main import app
from fantasy_helper.domain.league import Platform
from fantasy_helper.domain.users import EspnCredentials, Role, User
from fantasy_helper.pipeline import onboarding
from fantasy_helper.storage import crypto
from fantasy_helper.storage.repositories import credentials, leagues
from fantasy_helper.storage.tables import LeagueRow, PlatformCredentialRow, UserRow

pytestmark = pytest.mark.db

KEY = crypto.load_key(crypto.generate_key())
CREDS = EspnCredentials(espn_s2=SecretStr("s2-secret-value"), swid=SecretStr("{ABC-123}"))
ESPN_LEAGUE = {
    "settings": {
        "name": "Points League",
        "size": 10,
        "scoringSettings": {"scoringType": "H2H_POINTS"},
    },
    "teams": [{"id": 7, "name": "Adam's Team", "owners": ["{ABC-123}"]}],
}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "_sleep", lambda _seconds: None)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def espn_client(status: int = 200) -> httpx.Client:
    return _http.make_client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(status, json=ESPN_LEAGUE))
    )


def make_user(session: Session) -> User:
    return onboarding.ensure_user(
        session,
        email=f"Owner-{uuid.uuid4().hex[:6]}@Example.com",
        display_name="Adam",
        timezone="America/New_York",
    )


def test_adding_an_espn_league_saves_it_and_encrypted_cookies(session: Session) -> None:
    user = make_user(session)
    assert user.role is Role.OWNER
    assert user.email == user.email.lower()
    added = onboarding.add_espn_league(
        session, espn_client(), user=user, league_id="555", season=2027, key=KEY,
        new_credentials=CREDS,
    )  # fmt: skip
    assert added.created
    assert added.league.my_team_external_id == "7"
    assert added.my_team_label == "Adam's Team"
    stored = session.scalar(
        select(PlatformCredentialRow.ciphertext).where(PlatformCredentialRow.user_id == user.id)
    )
    assert stored is not None
    assert b"s2-secret-value" not in stored
    loaded = credentials.load_espn(session, user.id, KEY)
    assert loaded is not None
    assert loaded.espn_s2.get_secret_value() == "s2-secret-value"


def test_second_espn_league_reuses_saved_cookies_and_re_adding_updates(session: Session) -> None:
    user = make_user(session)
    onboarding.add_espn_league(
        session, espn_client(), user=user, league_id="555", season=2027, key=KEY,
        new_credentials=CREDS,
    )  # fmt: skip
    second = onboarding.add_espn_league(
        session, espn_client(), user=user, league_id="777", season=2027, key=KEY
    )
    again = onboarding.add_espn_league(
        session, espn_client(), user=user, league_id="777", season=2027, key=KEY
    )
    assert second.created
    assert not again.created
    assert [lg.external_id for lg in leagues.list_for_user(session, user.id)] == ["555", "777"]


def test_rejected_cookies_are_never_saved(session: Session) -> None:
    user = make_user(session)
    with pytest.raises(EspnAccessError):
        onboarding.add_espn_league(
            session, espn_client(401), user=user, league_id="555", season=2027, key=KEY,
            new_credentials=CREDS,
        )  # fmt: skip
    assert not credentials.has(session, user.id, Platform.ESPN)
    assert leagues.list_for_user(session, user.id) == []


def test_espn_without_any_saved_cookies_says_so(session: Session) -> None:
    user = make_user(session)
    with pytest.raises(onboarding.MissingCredentialsError):
        onboarding.add_espn_league(
            session, espn_client(), user=user, league_id="555", season=2027, key=KEY
        )


def test_adding_a_sleeper_league(session: Session) -> None:
    user = make_user(session)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/user/adam"):
            return httpx.Response(200, json={"user_id": "me"})
        if path.endswith("/rosters"):
            return httpx.Response(200, json=[{"roster_id": 3, "owner_id": "me"}])
        return httpx.Response(
            200,
            json={
                "league_id": "9",
                "name": "Test",
                "season": "2026",
                "sport": "nba",
                "total_rosters": 8,
            },
        )

    client = _http.make_client(transport=httpx.MockTransport(handler))
    added = onboarding.add_sleeper_league(
        session, client, user=user, league_id="9", username="adam"
    )
    assert added.league.season == 2027
    assert added.league.my_team_external_id == "3"


def test_cli_prompts_hide_cookies_and_store_everything(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.begin() as connection:  # start from no users, so the CLI creates the owner
        connection.execute(text("delete from users"))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FH_ENCRYPTION_KEY", crypto.generate_key())
    monkeypatch.setattr(leagues_cli, "make_client", lambda: contextlib.nullcontext(espn_client()))

    answers = "adam@example.com\nAdam\n\ns2-secret-value\n{ABC-123}\n"
    result = CliRunner().invoke(
        app, ["league", "add", "espn", "555", "--season", "2027"], input=answers
    )
    assert result.exit_code == 0, result.output
    assert "Added espn league 'Points League'" in result.output
    assert "Adam's Team" in result.output
    assert "s2-secret-value" not in result.output  # hidden prompt: never echoed
    with Session(engine) as session:
        owner = session.scalar(select(UserRow).where(UserRow.email == "adam@example.com"))
        assert owner is not None
        assert owner.timezone == "America/New_York"
        assert (
            session.scalar(select(LeagueRow.name).where(LeagueRow.user_id == owner.id))
            == "Points League"
        )

    listed = CliRunner().invoke(app, ["league", "list"])
    assert "555" in listed.output
    assert "Points League" in listed.output
