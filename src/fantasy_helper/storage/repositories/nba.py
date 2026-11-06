"""League-independent NBA data: the schedule, projection snapshots and injury status changes."""

import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.projections import InjuryStatus, ProjectionScope, Provider
from fantasy_helper.domain.records import InjuryRecord, ProjectionRecord, ScheduledGameRecord
from fantasy_helper.storage.tables import (
    InjuryObservationRow,
    NbaGameRow,
    ProjectionSnapshotRow,
    ProjectionValueRow,
)


def upsert_games(session: Session, season: int, games: list[ScheduledGameRecord]) -> int:
    """Tip times move (postponements, flexed national games), so existing games are updated."""
    if not games:
        return 0
    stmt = pg_insert(NbaGameRow).values(
        [
            {"id": uuid.uuid4(), "season": season, "espn_game_id": g.external_id,
             "scoring_period": g.scoring_period, "tip_utc": g.tip_utc,
             "home_team": g.home_team, "away_team": g.away_team}
            for g in games
        ]
    )  # fmt: skip
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["espn_game_id"],
            set_={
                col: stmt.excluded[col]
                for col in ("season", "scoring_period", "tip_utc", "home_team", "away_team")
            }
            | {"updated_at": func.now()},
        )
    )
    return len(games)


def snapshot_loaded(session: Session, raw_key: str) -> bool:
    return (
        session.scalar(
            select(ProjectionSnapshotRow.id).where(ProjectionSnapshotRow.raw_key == raw_key)
        )
        is not None
    )


def load_projections(
    session: Session,
    *,
    provider: Provider,
    scope: ProjectionScope,
    season: int,
    period: str | None,
    pulled_at: datetime,
    raw_key: str,
    rows: list[tuple[ProjectionRecord, UUID]],
) -> int:
    """Store one raw snapshot's projections, once. Returns rows written (0 if already loaded)."""
    if snapshot_loaded(session, raw_key):
        return 0
    snapshot_id = uuid.uuid4()
    session.add(
        ProjectionSnapshotRow(
            id=snapshot_id, provider=provider.value, scope=scope.value, season=season,
            period=period, pulled_at=pulled_at, raw_key=raw_key,
        )
    )  # fmt: skip
    session.flush()
    unique = {(player_id, r.game_date): (r, player_id) for r, player_id in rows}
    if unique:
        session.execute(
            insert(ProjectionValueRow),
            [
                {"snapshot_id": snapshot_id, "player_id": player_id, "game_date": r.game_date,
                 "games": r.games, "per_game": {k.value: v for k, v in r.per_game.items()}}
                for r, player_id in unique.values()
            ],
        )  # fmt: skip
    return len(unique)


def latest_injury_observed_at(session: Session, provider: Provider) -> datetime | None:
    return session.scalar(
        select(func.max(InjuryObservationRow.observed_at)).where(
            InjuryObservationRow.provider == provider.value
        )
    )


def record_injury_changes(
    session: Session,
    *,
    provider: Provider,
    observed_at: datetime,
    raw_key: str,
    observations: list[tuple[InjuryRecord, UUID]],
) -> int:
    """Write a row only where status or note differs from the player's latest for this provider.

    A player seen for the first time is recorded only if not ACTIVE, so the log holds real
    events rather than one 'active' row per player.
    """
    latest_per_player = (
        select(
            InjuryObservationRow.player_id,
            InjuryObservationRow.status,
            InjuryObservationRow.note,
            func.row_number()
            .over(
                partition_by=InjuryObservationRow.player_id,
                order_by=InjuryObservationRow.observed_at.desc(),
            )
            .label("rank"),
        )
        .where(InjuryObservationRow.provider == provider.value)
        .subquery()
    )
    current = {
        pid: (status, note)
        for pid, status, note, _ in session.execute(
            select(latest_per_player).where(latest_per_player.c.rank == 1)
        )
    }
    changes = []
    for record, player_id in observations:
        before = current.get(player_id)
        after = (record.status.value, record.note)
        if before == after or (before is None and record.status is InjuryStatus.ACTIVE):
            continue
        current[player_id] = after
        changes.append(
            {"player_id": player_id, "provider": provider.value, "status": record.status.value,
             "note": record.note, "observed_at": observed_at, "raw_key": raw_key}
        )  # fmt: skip
    if changes:
        session.execute(insert(InjuryObservationRow), changes)
    return len(changes)
