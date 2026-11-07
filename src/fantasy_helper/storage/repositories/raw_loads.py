"""Which raw payloads have been loaded, for tables that don't keep their own raw key."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.storage.tables import RawLoadRow


def loaded_keys(session: Session, dataset: str) -> set[str]:
    return set(session.scalars(select(RawLoadRow.raw_key).where(RawLoadRow.dataset == dataset)))


def mark_loaded(session: Session, raw_key: str, *, dataset: str, rows: int) -> None:
    session.add(RawLoadRow(raw_key=raw_key, dataset=dataset, rows=rows))
    session.flush()
