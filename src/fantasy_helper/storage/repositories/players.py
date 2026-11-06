"""Canonical players and the identity crosswalk."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from fantasy_helper.domain.records import PlayerKey, SourcePlayer
from fantasy_helper.storage.tables import (
    InjuryObservationRow,
    PlayerExternalIdRow,
    PlayerRow,
    ProjectionValueRow,
    RosterEntryRow,
)


@dataclass(frozen=True)
class Link:
    player_id: UUID
    method: str


def load_links(session: Session) -> dict[tuple[str, str], Link]:
    rows = session.execute(
        select(PlayerExternalIdRow.source, PlayerExternalIdRow.external_id,
               PlayerExternalIdRow.player_id, PlayerExternalIdRow.match_method)
    )  # fmt: skip
    return {(source, ext): Link(pid, method) for source, ext, pid, method in rows}


def load_players(session: Session) -> dict[UUID, PlayerRow]:
    return {row.id: row for row in session.scalars(select(PlayerRow))}


def insert_players(session: Session, players: list[tuple[UUID, SourcePlayer]]) -> None:
    """Bulk insert new canonical players. Call before inserting links that point to them."""
    if players:
        session.execute(
            insert(PlayerRow),
            [
                {"id": player_id, "full_name": p.full_name, "nba_team": p.nba_team,
                 "positions": [pos.value for pos in p.positions]}
                for player_id, p in players
            ],
        )  # fmt: skip


def insert_links(session: Session, links: list[tuple[UUID, PlayerKey, str]]) -> None:
    if links:
        session.execute(
            insert(PlayerExternalIdRow),
            [
                {"source": key.source.value, "external_id": key.external_id,
                 "player_id": player_id, "match_method": method}
                for player_id, key, method in links
            ],
        )  # fmt: skip


def merge(session: Session, from_id: UUID, into_id: UUID, *, key: PlayerKey, method: str) -> None:
    """Fold a provisional canonical player into the one it turned out to be."""
    for table in (RosterEntryRow, ProjectionValueRow, InjuryObservationRow):
        session.execute(update(table).where(table.player_id == from_id).values(player_id=into_id))
    session.execute(
        update(PlayerExternalIdRow)
        .where(PlayerExternalIdRow.player_id == from_id)
        .values(player_id=into_id, match_method=method)
    )
    session.flush()
    session.execute(delete(PlayerRow).where(PlayerRow.id == from_id))
