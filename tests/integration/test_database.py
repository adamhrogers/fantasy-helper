import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session

from fantasy_helper.domain.league import Platform
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus, SourceFreshness
from fantasy_helper.storage import crypto, migrate
from fantasy_helper.storage.repositories.runs import record_run
from fantasy_helper.storage.tables import LeagueRow, PlatformCredentialRow, RunRow, UserRow

pytestmark = pytest.mark.db


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def add_user(session: Session) -> UserRow:
    user = UserRow(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Adam",
        timezone="America/New_York",
        role="owner",
    )
    session.add(user)
    session.flush()
    return user


def test_migrations_reach_head(engine: Engine) -> None:
    assert migrate.current_revision(engine) == migrate.head_revision()
    tables = set(inspect(engine).get_table_names())
    assert {"users", "leagues", "platform_credentials", "runs"} <= tables


def test_every_table_has_row_level_security(engine: Engine) -> None:
    """Supabase's public Data API must not be able to read any table (ADR 0007)."""
    with engine.connect() as connection:
        unprotected = connection.execute(
            text(
                "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " where n.nspname = 'public' and c.relkind = 'r'"
                " and not c.relrowsecurity and c.relname <> 'alembic_version'"
            )
        ).scalars()
        assert list(unprotected) == []


def test_credentials_are_stored_encrypted(session: Session) -> None:
    user = add_user(session)
    key = crypto.load_key(crypto.generate_key())
    aad = crypto.credential_aad(user.id, Platform.ESPN)
    plaintext = json.dumps({"espn_s2": "secret-s2-cookie", "swid": "{SWID}"}).encode()
    session.add(
        PlatformCredentialRow(
            id=uuid.uuid4(),
            user_id=user.id,
            platform=Platform.ESPN.value,
            ciphertext=crypto.encrypt(plaintext, key=key, aad=aad),
        )
    )
    session.flush()

    stored = session.execute(
        text("select ciphertext from platform_credentials where user_id = :u"), {"u": user.id}
    ).scalar_one()
    assert b"secret-s2-cookie" not in stored
    assert crypto.decrypt(stored, key=key, aad=aad) == plaintext


def test_deleting_a_user_removes_their_leagues_and_credentials(session: Session) -> None:
    user = add_user(session)
    session.add_all(
        [
            LeagueRow(
                id=uuid.uuid4(),
                user_id=user.id,
                platform="espn",
                external_id="123",
                season=2027,
                name="Points League",
            ),
            PlatformCredentialRow(
                id=uuid.uuid4(), user_id=user.id, platform="espn", ciphertext=b"\x01"
            ),
        ]
    )
    session.flush()
    session.execute(text("delete from users where id = :u"), {"u": user.id})
    assert session.scalars(select(LeagueRow).where(LeagueRow.user_id == user.id)).all() == []
    assert (
        session.scalars(
            select(PlatformCredentialRow).where(PlatformCredentialRow.user_id == user.id)
        ).all()
        == []
    )


def test_downgrade_then_upgrade_is_clean(database_url: str, engine: Engine) -> None:
    migrate.downgrade(database_url, "base")
    assert "users" not in inspect(engine).get_table_names()
    migrate.upgrade(database_url, "head")
    assert migrate.current_revision(engine) == migrate.head_revision()


def test_run_log_records_a_snapshot(session: Session) -> None:
    started = datetime(2026, 11, 6, 11, 30, tzinfo=UTC)
    run = RunRecord(
        kind=RunKind.SNAPSHOT,
        status=RunStatus.DEGRADED,
        started_at=started,
        ended_at=started + timedelta(seconds=40),
        sources=(SourceFreshness(source="espn.players_projections", pulled_at=started, rows=600),),
        rows_loaded=600,
        errors=("sleeper.players: HTTP 503",),
    )
    record_run(session, run)
    session.flush()
    row = session.get(RunRow, run.id)
    assert row is not None
    assert (row.kind, row.status, row.rows_loaded) == ("snapshot", "degraded", 600)
    assert row.sources[0]["source"] == "espn.players_projections"
    assert row.errors == ["sleeper.players: HTTP 503"]
