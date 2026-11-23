"""The API against Postgres: sign-in, who sees what, and push subscriptions (ADR 0029)."""

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from fantasy_helper.api.app import create_app
from fantasy_helper.api.auth import TokenVerifier
from fantasy_helper.config import Settings
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.domain.users import Role, User
from fantasy_helper.storage.repositories import leagues, push_subscriptions, users
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.tables import UserRow

pytestmark = pytest.mark.db

ISSUER = "https://abc.supabase.co/auth/v1"
KEY = ec.generate_private_key(ec.SECP256R1())
DAY = date(2026, 11, 15)


def bearer(sub: UUID, email: str, **claims: Any) -> dict[str, str]:
    body = {"iss": ISSUER, "aud": "authenticated", "sub": str(sub), "email": email,
            "exp": time.time() + 3600} | claims  # fmt: skip
    return {"Authorization": "Bearer " + jwt.encode(body, KEY, algorithm="ES256")}


class Person:
    """A user in the database, with one league and one saved daily report."""

    def __init__(self, session: Session, name: str) -> None:
        self.email = f"{uuid4().hex[:8]}@example.com"
        self.sub = uuid4()  # what Supabase will call them
        self.user = users.create(
            session,
            User(email=self.email, display_name=name, timezone="America/New_York",
                 role=Role.BETA),
        )  # fmt: skip
        self.league, _ = leagues.upsert(
            session,
            League(user_id=self.user.id, platform=Platform.SLEEPER, external_id=uuid4().hex[:10],
                   season=2027, name=f"{name}'s league"),
        )  # fmt: skip
        saved_reports.save(
            session,
            ReportDocument(
                user_id=self.user.id, league_id=self.league.id, kind=ReportKind.DAILY,
                for_date=DAY, generated_at=datetime(2026, 11, 15, 11, 30, tzinfo=UTC),
                cards=(Card(id="c1", kind=CardKind.RECAP, title=f"{name}'s recap", body="x"),),
            ),
        )  # fmt: skip

    def headers(self) -> dict[str, str]:
        return bearer(self.sub, self.email)


@pytest.fixture
def people(engine: Engine) -> Iterator[tuple[Person, Person]]:
    with Session(engine) as session, session.begin():
        a, b = Person(session, "Ana"), Person(session, "Ben")
    yield a, b
    with Session(engine) as session, session.begin():  # the API committed: clean up by hand
        session.execute(delete(UserRow).where(UserRow.id.in_([a.user.id, b.user.id])))


def make_client(engine: Engine, **settings: Any) -> TestClient:
    verifier = TokenVerifier(issuer=ISSUER, key_for=lambda _t: KEY.public_key())
    app = create_app(Settings(**settings), verifier=verifier, engine_factory=lambda: engine)
    return TestClient(app)


def test_livez_needs_no_sign_in(engine: Engine) -> None:
    with make_client(engine) as client:
        assert client.get("/livez").json()["status"] == "ok"


def test_nothing_else_opens_without_a_valid_token(engine: Engine) -> None:
    with make_client(engine) as client:
        for path in ("/v1/me", "/v1/reports/daily", "/v1/push/public-key"):
            assert client.get(path).status_code == 401
        assert client.get("/v1/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
        assert client.put("/v1/push/subscriptions", json={}).status_code == 401


def test_the_first_sign_in_links_the_user_and_later_ones_use_the_id(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, _ = people
    with make_client(engine) as client:
        assert client.get("/v1/me", headers=a.headers()).json()["name"] == "Ana"
        with Session(engine) as session:
            assert users.get_by_auth_id(session, a.sub) is not None
        # The id is what's trusted now: an email change at Supabase changes nothing.
        renamed = bearer(a.sub, "new-address@example.com")
        assert client.get("/v1/me", headers=renamed).status_code == 200


def test_a_sign_in_that_isnt_one_of_ours_is_refused_and_creates_nothing(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, _ = people
    with make_client(engine) as client:
        stranger = bearer(uuid4(), f"{uuid4().hex[:8]}@example.com")
        assert client.get("/v1/me", headers=stranger).status_code == 403
        anonymous = bearer(uuid4(), a.email, is_anonymous=True)
        assert client.get("/v1/me", headers=anonymous).status_code == 401
        assert client.get("/v1/me", headers=a.headers()).status_code == 200  # a links
        # A different sign-in presenting a's email can't take a over.
        thief = bearer(uuid4(), a.email)
        assert client.get("/v1/me", headers=thief).status_code == 403
        assert client.get("/v1/me", headers=a.headers()).status_code == 200


def test_each_user_sees_only_their_own_leagues_and_reports(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, b = people
    with make_client(engine) as client:
        mine = client.get("/v1/me", headers=a.headers()).json()
        assert [lg["name"] for lg in mine["leagues"]] == ["Ana's league"]
        got = client.get("/v1/reports/daily", headers=a.headers()).json()
        assert got["for_date"] == "2026-11-15"
        # The day's games come with the daily reports (none are scheduled in this test).
        assert got["games"] == {"day": "2026-11-15", "games": 0, "first_tip_utc": None}
        assert [c["title"] for r in got["reports"] for c in r["cards"]] == ["Ana's recap"]
        assert {r["league_id"] for r in got["reports"]} == {str(a.league.id)}

        theirs = client.get("/v1/reports/daily", headers=b.headers()).json()
        assert [c["title"] for r in theirs["reports"] for c in r["cards"]] == ["Ben's recap"]

        # A date with nothing saved is an empty answer, not an error and not someone else's.
        empty = client.get("/v1/reports/weekly", headers=a.headers()).json()
        assert empty == {"for_date": None, "reports": [], "games": None}
        assert client.get("/v1/reports/daily?date=2020-01-01", headers=a.headers()).json() == {
            "for_date": None,
            "reports": [],
            "games": None,
        }
        assert client.get("/v1/reports/scorecard", headers=a.headers()).status_code == 422


def test_push_subscriptions_belong_to_one_user_each(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, b = people
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid4().hex}"
    sub = {"endpoint": endpoint, "keys": {"p256dh": "key", "auth": "secret"}}
    with make_client(engine, FH_VAPID_PUBLIC_KEY="BPublicKey") as client:
        assert (
            client.put("/v1/push/subscriptions", headers=a.headers(), json=sub).status_code == 200
        )
        with Session(engine) as session:
            assert push_subscriptions.endpoints_for_user(session, a.user.id) == [endpoint]

        # Ben can't remove Ana's device...
        gone = client.request(
            "DELETE", "/v1/push/subscriptions", headers=b.headers(), json={"endpoint": endpoint}
        )
        assert gone.status_code == 204
        with Session(engine) as session:
            assert push_subscriptions.endpoints_for_user(session, a.user.id) == [endpoint]

        # ...but the same phone signing in as Ben moves to Ben, with no duplicate.
        assert (
            client.put("/v1/push/subscriptions", headers=b.headers(), json=sub).status_code == 200
        )
        with Session(engine) as session:
            assert push_subscriptions.endpoints_for_user(session, a.user.id) == []
            assert push_subscriptions.endpoints_for_user(session, b.user.id) == [endpoint]

        removed = client.request(
            "DELETE", "/v1/push/subscriptions", headers=b.headers(), json={"endpoint": endpoint}
        )
        assert removed.status_code == 204
        with Session(engine) as session:
            assert push_subscriptions.endpoints_for_user(session, b.user.id) == []

        assert client.get("/v1/push/public-key", headers=a.headers()).json() == {
            "public_key": "BPublicKey"
        }


def test_a_push_subscription_must_be_https_and_complete(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, _ = people
    with make_client(engine) as client:
        plain = {"endpoint": "http://fcm.googleapis.com/x1", "keys": {"p256dh": "k", "auth": "a"}}
        assert (
            client.put("/v1/push/subscriptions", headers=a.headers(), json=plain).status_code == 422
        )
        partial = {"endpoint": "https://fcm.googleapis.com/x1", "keys": {"p256dh": "k"}}
        assert (
            client.put("/v1/push/subscriptions", headers=a.headers(), json=partial).status_code
            == 422
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://push.example.com/send/x1234",  # any other server
        "https://web.push.apple.com.attacker.example/x",  # a known host as a prefix
        "https://attacker.example/web.push.apple.com/x",  # a known host in the path
        "https://web.push.apple.com@attacker.example/x",  # a user name that looks like the host
        "https://web.push.apple.com:8443/x",  # a port
        "https://169.254.169.254/computeMetadata/v1/",  # the cloud's metadata server
    ],
)
def test_a_push_subscription_is_saved_only_on_a_known_push_service(
    engine: Engine, people: tuple[Person, Person], endpoint: str
) -> None:
    """The report jobs send to whatever is saved here, so anything but a push service's address is
    refused before it is written (ADR 0030)."""
    a, _ = people
    sub = {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}
    with make_client(engine) as client:
        assert (
            client.put("/v1/push/subscriptions", headers=a.headers(), json=sub).status_code == 422
        )
        with Session(engine) as session:
            assert push_subscriptions.endpoints_for_user(session, a.user.id) == []


def test_each_known_push_service_is_accepted(engine: Engine, people: tuple[Person, Person]) -> None:
    a, _ = people
    endpoints = [
        f"https://web.push.apple.com/{uuid4().hex}",
        f"https://fcm.googleapis.com/fcm/send/{uuid4().hex}",
        f"https://updates.push.services.mozilla.com/wpush/v2/{uuid4().hex}",
        f"https://wns2-bl2p.notify.windows.com/w/?token={uuid4().hex}",
    ]
    with make_client(engine) as client:
        for endpoint in endpoints:
            sub = {"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}}
            put = client.put("/v1/push/subscriptions", headers=a.headers(), json=sub)
            assert put.status_code == 200, endpoint
        with Session(engine) as session:
            assert sorted(push_subscriptions.endpoints_for_user(session, a.user.id)) == sorted(
                endpoints
            )


def test_the_push_key_is_unavailable_until_it_is_set(
    engine: Engine, people: tuple[Person, Person]
) -> None:
    a, _ = people
    with make_client(engine) as client:
        assert client.get("/v1/push/public-key", headers=a.headers()).status_code == 503


def test_an_app_with_no_sign_in_configured_refuses_everyone(engine: Engine) -> None:
    app = create_app(Settings(), engine_factory=lambda: engine)  # no verifier, no Supabase URL
    with TestClient(app) as client:
        assert client.get("/livez").status_code == 200
        assert client.get("/v1/me", headers=bearer(uuid4(), "a@example.com")).status_code == 503
