"""Saved reports: what the scheduled runs built, read back for the phone app and `fh report open`
(ADR 0026). One row per league, kind and date, replaced when a run repeats."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Select, and_, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import NBA_TIMEZONE
from fantasy_helper.domain.reports import GameDay, ReportDocument, ReportKind
from fantasy_helper.storage.tables import LeagueRow, NbaGameRow, ReportRow


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
    rows = session.scalars(_reports(user_id, kind, for_date))
    return [to_domain(row) for row in rows]


def with_game_day(
    session: Session, user_id: UUID, kind: ReportKind, for_date: date | None = None
) -> tuple[list[ReportDocument], GameDay | None]:
    """`for_user`'s reports, plus that date's NBA schedule: how many games, and when the first
    tips. Still one query: the schedule comes from two small subqueries on `nba_games`' indexed
    tip-off time. None when there are no reports."""
    day = ReportRow.for_date
    start = func.timezone(NBA_TIMEZONE.key, cast(day, DateTime))  # midnight Eastern, as UTC
    end = func.timezone(NBA_TIMEZONE.key, cast(day, DateTime) + timedelta(days=1))
    on_day = and_(NbaGameRow.tip_utc >= start, NbaGameRow.tip_utc < end)
    first_tip = select(func.min(NbaGameRow.tip_utc)).where(on_day).scalar_subquery()
    games = select(func.count()).select_from(NbaGameRow).where(on_day).scalar_subquery()
    rows = session.execute(_reports(user_id, kind, for_date, first_tip, games)).all()
    if not rows:
        return [], None
    row, tip, count = rows[0]
    return [to_domain(r[0]) for r in rows], GameDay(
        day=row.for_date, games=count, first_tip_utc=tip
    )


def _reports(user_id: UUID, kind: ReportKind, for_date: date | None, *columns: Any) -> Select[Any]:
    day = (
        for_date
        if for_date is not None
        else select(func.max(ReportRow.for_date))
        .where(ReportRow.user_id == user_id, ReportRow.kind == kind.value)
        .scalar_subquery()
    )
    return (
        select(ReportRow, *columns)
        .join(LeagueRow, LeagueRow.id == ReportRow.league_id)
        .where(
            ReportRow.user_id == user_id, ReportRow.kind == kind.value, ReportRow.for_date == day
        )
        .order_by(LeagueRow.name, ReportRow.league_id)
    )


def to_domain(row: ReportRow) -> ReportDocument:
    return ReportDocument.model_validate(
        {
            "id": row.id, "user_id": row.user_id, "league_id": row.league_id,
            "kind": row.kind, "for_date": row.for_date, "generated_at": row.generated_at,
            "cards": row.cards, "engine_only": row.engine_only,
        }
    )  # fmt: skip


@dataclass(frozen=True)
class Announcement:
    """What a report push would announce: the reports of one kind for some dates, as saved."""

    reports: int
    cards: int
    already_sent: bool  # a push for any of them was already accepted (ADR 0030)


def announcement(
    session: Session, user_id: UUID, kind: ReportKind, dates: Collection[date]
) -> Announcement:
    """Counts for the push, read from what is committed, and whether it already went out."""
    total_cards, total_reports, sent = session.execute(
        select(
            func.coalesce(func.sum(func.jsonb_array_length(ReportRow.cards)), 0),
            func.count(ReportRow.id),
            func.count(ReportRow.notified_at),
        ).where(
            ReportRow.user_id == user_id,
            ReportRow.kind == kind.value,
            ReportRow.for_date.in_(dates),
        )
    ).one()
    return Announcement(reports=total_reports, cards=int(total_cards), already_sent=sent > 0)


def mark_notified(
    session: Session, user_id: UUID, kind: ReportKind, dates: Collection[date], at: datetime
) -> int:
    """Record that the push for these reports went out. Rows already marked keep their time."""
    result = session.execute(
        update(ReportRow)
        .where(
            ReportRow.user_id == user_id,
            ReportRow.kind == kind.value,
            ReportRow.for_date.in_(dates),
            ReportRow.notified_at.is_(None),
        )
        .values(notified_at=at)
        .returning(ReportRow.id)
    )
    return len(result.all())
