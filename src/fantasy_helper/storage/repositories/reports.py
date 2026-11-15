"""Saved reports: what the scheduled runs built, read back for the phone app and `fh report open`
(ADR 0026). One row per league, kind and date, replaced when a run repeats."""

from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.reports import ReportDocument, ReportKind
from fantasy_helper.storage.tables import LeagueRow, ReportRow


def save(session: Session, report: ReportDocument) -> UUID:
    """Store a report, or replace the one already saved for the same league, kind and date.
    The saved row keeps its first id (feedback will refer to it), and takes the new cards.
    Returns that id."""
    cards = [card.model_dump(mode="json") for card in report.cards]
    stmt = (
        pg_insert(ReportRow)
        .values(
            id=report.id,
            user_id=report.user_id,
            league_id=report.league_id,
            kind=report.kind.value,
            for_date=report.for_date,
            generated_at=report.generated_at,
            engine_only=report.engine_only,
            cards=cards,
        )
        .on_conflict_do_update(
            index_elements=["league_id", "kind", "for_date"],
            set_={
                "generated_at": report.generated_at,
                "engine_only": report.engine_only,
                "cards": cards,
            },
        )
        .returning(ReportRow.id)
    )
    return session.execute(stmt).scalar_one()


def latest_date(session: Session, user_id: UUID, kind: ReportKind) -> date | None:
    return session.scalar(
        select(func.max(ReportRow.for_date)).where(
            ReportRow.user_id == user_id, ReportRow.kind == kind.value
        )
    )


def for_user(
    session: Session, user_id: UUID, kind: ReportKind, for_date: date | None = None
) -> list[ReportDocument]:
    """A user's reports of one kind for one date (default: the latest date that has any), one per
    league, by league name. One query either way: the phone app calls this on every open (the hot
    read path, ADR 0020), so the latest date is a subquery, not a first round trip."""
    day = (
        for_date
        if for_date is not None
        else select(func.max(ReportRow.for_date))
        .where(ReportRow.user_id == user_id, ReportRow.kind == kind.value)
        .scalar_subquery()
    )
    rows = session.scalars(
        select(ReportRow)
        .join(LeagueRow, LeagueRow.id == ReportRow.league_id)
        .where(
            ReportRow.user_id == user_id, ReportRow.kind == kind.value, ReportRow.for_date == day
        )
        .order_by(LeagueRow.name, ReportRow.league_id)
    )
    return [to_domain(row) for row in rows]


def to_domain(row: ReportRow) -> ReportDocument:
    return ReportDocument.model_validate(
        {
            "id": row.id, "user_id": row.user_id, "league_id": row.league_id,
            "kind": row.kind, "for_date": row.for_date, "generated_at": row.generated_at,
            "cards": row.cards, "engine_only": row.engine_only,
        }
    )  # fmt: skip
