"""Saved reports and the run lock, against Postgres (ADR 0026)."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from fantasy_helper.cli.main import app
from fantasy_helper.domain.league import League, Platform
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.storage.repositories import database, leagues, users
from fantasy_helper.storage.repositories import reports as saved_reports
from fantasy_helper.storage.tables import LeagueRow, ReportRow, UserRow
from tests.integration.test_ingest import count, setup_owner

pytestmark = pytest.mark.db

GENERATED = datetime(2026, 11, 15, 11, 45, tzinfo=UTC)


@pytest.fixture
def session(engine: Engine):  # type: ignore[no-untyped-def]
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def report(
    user_id: UUID,
    league_id: UUID,
    *,
    kind: ReportKind = ReportKind.DAILY,
    day: date = date(2026, 11, 15),
    title: str = "Lock Anthony Edwards's 41.5",
) -> ReportDocument:
    card = Card(id="c1", kind=CardKind.LOCK_OR_PASS, title=title, body="body", reason="why")
    return ReportDocument(
        user_id=user_id, league_id=league_id, kind=kind, for_date=day,
        generated_at=GENERATED, cards=(card,), engine_only=True,
    )  # fmt: skip


def test_a_saved_report_reads_back_exactly(session: Session) -> None:
    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    built = report(owner.id, league.id)
    saved_reports.save(session, built)
    (stored,) = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    assert stored == built


def test_a_repeat_replaces_the_cards_and_keeps_the_first_id(session: Session) -> None:
    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    first = saved_reports.save(session, report(owner.id, league.id, title="Lock A"))
    second = saved_reports.save(session, report(owner.id, league.id, title="Lock B"))
    assert first == second  # feedback will refer to this id, so it survives a rerun
    (stored,) = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    assert stored.id == first
    assert stored.cards[0].title == "Lock B"
    assert count(session, ReportRow.id) == 1


def test_reads_default_to_the_latest_date_and_filter_by_kind(session: Session) -> None:
    owner = setup_owner(session)
    one, two = leagues.list_for_user(session, owner.id)[:2]
    saved_reports.save(session, report(owner.id, one.id, day=date(2026, 11, 14)))
    saved_reports.save(session, report(owner.id, one.id, day=date(2026, 11, 15)))
    saved_reports.save(session, report(owner.id, two.id, day=date(2026, 11, 15)))
    saved_reports.save(session, report(owner.id, one.id, kind=ReportKind.WEEKLY))

    latest = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    assert {r.for_date for r in latest} == {date(2026, 11, 15)}
    assert {r.league_id for r in latest} == {one.id, two.id}
    earlier = saved_reports.for_user(session, owner.id, ReportKind.DAILY, date(2026, 11, 14))
    assert [r.league_id for r in earlier] == [one.id]
    assert len(saved_reports.for_user(session, owner.id, ReportKind.WEEKLY)) == 1
    assert saved_reports.for_user(session, owner.id, ReportKind.DAILY, date(2026, 1, 1)) == []


def test_the_latest_reports_come_in_one_query_in_league_name_order(
    session: Session, engine: Engine
) -> None:
    """The phone app reads this on every open (ADR 0020), so it's one round trip, not two."""
    owner = setup_owner(session)
    mine = leagues.list_for_user(session, owner.id)
    for lg in mine:
        saved_reports.save(session, report(owner.id, lg.id, day=date(2026, 11, 15)))
    saved_reports.save(session, report(owner.id, mine[0].id, day=date(2026, 11, 14)))
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_rest):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        found = saved_reports.for_user(session, owner.id, ReportKind.DAILY)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1
    names = {lg.id: lg.name for lg in mine}
    assert [names[r.league_id] for r in found] == sorted(names[lg.id] for lg in mine)
    assert {r.for_date for r in found} == {date(2026, 11, 15)}  # the latest date, in the same query


def test_one_users_reports_are_never_anothers(session: Session) -> None:
    owner = setup_owner(session)
    other = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    saved_reports.save(session, report(owner.id, league.id))
    assert saved_reports.for_user(session, other.id, ReportKind.DAILY) == []
    assert saved_reports.latest_date(session, other.id, ReportKind.DAILY) is None


def test_deleting_a_league_removes_its_reports(session: Session) -> None:
    owner = setup_owner(session)
    league = leagues.list_for_user(session, owner.id)[0]
    saved_reports.save(session, report(owner.id, league.id))
    session.execute(delete(LeagueRow).where(LeagueRow.id == league.id))
    assert count(session, ReportRow.id) == 0  # a beta friend's data must stay deletable


def test_a_report_needs_a_real_league(session: Session) -> None:
    owner = setup_owner(session)
    with pytest.raises(Exception, match="foreign key"):
        saved_reports.save(session, report(owner.id, uuid4()))
    session.rollback()


def test_a_second_run_waits_for_the_first_and_then_gives_up(engine: Engine) -> None:
    """Two runs that write at once (a double start, an overlap) take turns."""
    with Session(engine) as holder, holder.begin():
        database.lock_pipeline(holder)
        with Session(engine) as waiter, waiter.begin(), pytest.raises(OperationalError):
            database.lock_pipeline(waiter, timeout="200ms")  # the holder still has it
    with Session(engine) as later, later.begin():  # released when the holder's transaction ended
        database.lock_pipeline(later, timeout="200ms")


def test_the_lock_timeout_applies_only_to_the_run_that_set_it(engine: Engine) -> None:
    with Session(engine) as run, run.begin():
        database.lock_pipeline(run, timeout="3s")
        assert run.execute(text("SHOW lock_timeout")).scalar_one() == "3s"
    with Session(engine) as other, other.begin():
        assert other.execute(text("SHOW lock_timeout")).scalar_one() == "0"  # SET LOCAL


def test_fh_migrate_and_fh_report_open_against_a_real_database(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The commands the cloud jobs and the laptop use, end to end: the schema is at head, a
    saved report opens as a page, and an empty day says so instead of failing oddly."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("FH_DATA_DIR", str(tmp_path))
    runner = CliRunner()

    migrated = runner.invoke(app, ["migrate"])
    assert migrated.exit_code == 0, migrated.output
    assert "already at head (0008)" in migrated.output

    # Other tests commit an owner of their own, and the command reads "the owner": use that one
    # if it's there, and leave nothing of ours behind either way.
    with Session(engine) as session, session.begin():
        existing = users.get_owner(session)
        owner = existing or setup_owner(session)
        mine, _ = leagues.upsert(
            session,
            League(
                user_id=owner.id, platform=Platform.SLEEPER, external_id=uuid4().hex,
                season=2027, name="Report test league",
            ),
        )  # fmt: skip
        saved_reports.save(session, report(owner.id, mine.id, title="Lock Jalen Brunson's 38.5"))
    try:
        opened = runner.invoke(app, ["report", "open", "daily", "--print-only"])
        assert opened.exit_code == 0, opened.output
        assert "for 2026-11-15" in opened.output
        page = next((tmp_path / "reports" / "2026-11-15").glob("daily-saved.html"))
        assert "Lock Jalen Brunson&#x27;s 38.5" in page.read_text()

        missing = runner.invoke(app, ["report", "open", "weekly", "--print-only"])
        assert missing.exit_code == 1
        assert "no saved weekly reports yet" in missing.output
        bad = runner.invoke(app, ["report", "open", "daily", "--date", "yesterday"])
        assert bad.exit_code == 1
        assert "--date must look like" in bad.output
    finally:
        with Session(engine) as session, session.begin():
            session.execute(delete(LeagueRow).where(LeagueRow.id == mine.id))  # takes its reports
            if existing is None:
                session.execute(delete(UserRow).where(UserRow.id == owner.id))
