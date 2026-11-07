"""The read models agents and the app read with one lookup (ADR 0020)."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.facts import FactSheet
from fantasy_helper.storage.tables import FactSheetRow, LeaguePlayerValueRow


def save_fact_sheet(session: Session, sheet: FactSheet) -> bool:
    """Store a sheet once; the same facts again (same hash) are a no-op. True if stored."""
    stmt = (
        pg_insert(FactSheetRow)
        .values(
            id=sheet.id,
            league_id=sheet.league_id,
            kind=sheet.kind.value,
            week=sheet.week,
            as_of=sheet.as_of,
            content_hash=sheet.content_hash,
            sheet=sheet.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=["league_id", "kind", "content_hash"])
        .returning(FactSheetRow.id)
    )
    return session.execute(stmt).scalar() is not None


def latest_fact_sheet(session: Session, league_id: UUID, kind: str) -> FactSheet | None:
    row = session.scalars(
        select(FactSheetRow)
        .where(FactSheetRow.league_id == league_id, FactSheetRow.kind == kind)
        .order_by(FactSheetRow.as_of.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return FactSheet.model_validate({k: v for k, v in row.sheet.items() if k != "content_hash"})


def replace_player_values(
    session: Session, league_id: UUID, rows: Sequence[dict[str, object]]
) -> int:
    session.execute(delete(LeaguePlayerValueRow).where(LeaguePlayerValueRow.league_id == league_id))
    if rows:
        session.execute(insert(LeaguePlayerValueRow), [{"league_id": league_id, **r} for r in rows])
    return len(rows)
