"""League-independent NBA data: the schedule, box scores, projection snapshots and injury
status changes."""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.calendar import nba_game_date
from fantasy_helper.domain.projections import InjuryStatus, ProjectionScope, Provider
from fantasy_helper.domain.records import (
    GameLogRecord,
    InjuryRecord,
    ProjectionRecord,
    ScheduledGameRecord,
)
from fantasy_helper.storage.tables import (
    STAT_COLUMNS,
    InjuryObservationRow,
    NbaGameRow,
    PlayerGameStatRow,
    ProjectionSnapshotRow,
    ProjectionValueRow,
    stat_values,
)

_NO_STATS = dict.fromkeys(STAT_COLUMNS.values())
# A multi-row INSERT binds every value as a parameter, and Postgres allows 65,535 per statement.
# Box score rows have 26 columns, so 1,000 rows (26,000 parameters) per statement.
GAME_LOG_CHUNK = 1000


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


def game_index(session: Session, season: int) -> dict[str, tuple[UUID, date]]:
    """ESPN game ID -> (our game ID, the game's date in Eastern time), for one season."""
    rows = session.execute(
        select(NbaGameRow.espn_game_id, NbaGameRow.id, NbaGameRow.tip_utc).where(
            NbaGameRow.season == season
        )
    )
    return {espn_id: (game_id, nba_game_date(tip)) for espn_id, game_id, tip in rows}


def upsert_game_logs(session: Session, rows: list[tuple[GameLogRecord, UUID, UUID, date]]) -> int:
    """Box score rows as (record, player ID, game ID, game date). Re-loading a game overwrites
    it, so a later stat correction wins."""
    if not rows:
        return 0
    values = {
        (player_id, game_id): {
            "player_id": player_id, "game_id": game_id, "season": r.season,
            "game_date": game_date, "scoring_period": r.scoring_period, "team": r.team,
            "played": r.played,
        } | _NO_STATS | stat_values(r.stats)
        for r, player_id, game_id, game_date in rows
    }  # fmt: skip
    updated = ["season", "game_date", "scoring_period", "team", "played", *STAT_COLUMNS.values()]
    batch = list(values.values())
    for start in range(0, len(batch), GAME_LOG_CHUNK):
        stmt = pg_insert(PlayerGameStatRow).values(batch[start : start + GAME_LOG_CHUNK])
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["player_id", "game_id"],
                set_={col: stmt.excluded[col] for col in updated} | {"updated_at": func.now()},
            )
        )
    return len(values)


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
                 "games": r.games} | _NO_STATS | stat_values(r.per_game)
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


# ------------------------------------------------------------------------------- reading


def season_start(session: Session, season: int) -> date | None:
    """Opening night: the first game's date in Eastern time."""
    tip = session.scalar(select(func.min(NbaGameRow.tip_utc)).where(NbaGameRow.season == season))
    return nba_game_date(tip) if tip is not None else None


def team_games(
    session: Session, season: int, team: str, first: date, last: date
) -> list[tuple[UUID, datetime, date]]:
    """(game ID, tip UTC, game date) for one team's games between two dates, in order."""
    rows = session.execute(
        select(NbaGameRow.id, NbaGameRow.tip_utc)
        .where(
            NbaGameRow.season == season,
            (NbaGameRow.home_team == team) | (NbaGameRow.away_team == team),
        )
        .order_by(NbaGameRow.tip_utc)
    )
    out = []
    for game_id, tip in rows:
        day = nba_game_date(tip)
        if first <= day <= last:
            out.append((game_id, tip, day))
    return out


def box_scores(
    session: Session, season: int, player_ids: Sequence[UUID] | None = None
) -> list[PlayerGameStatRow]:
    """Box score rows for a season, for some players or (None) everyone. One truth for every
    league (ADR 0022)."""
    query = select(PlayerGameStatRow).where(PlayerGameStatRow.season == season)
    if player_ids is not None:
        query = query.where(PlayerGameStatRow.player_id.in_(player_ids))
    return list(session.scalars(query.order_by(PlayerGameStatRow.game_date)))


def latest_projection_snapshot(
    session: Session,
    provider: Provider,
    scope: ProjectionScope,
    period: str | None,
    as_of: datetime,
) -> UUID | None:
    query = select(ProjectionSnapshotRow.id).where(
        ProjectionSnapshotRow.provider == provider.value,
        ProjectionSnapshotRow.scope == scope.value,
        ProjectionSnapshotRow.pulled_at <= as_of,
    )
    if period is not None:
        query = query.where(ProjectionSnapshotRow.period == period)
    return session.scalar(query.order_by(ProjectionSnapshotRow.pulled_at.desc()).limit(1))


def projection_rows(
    session: Session, snapshot_id: UUID, player_ids: Sequence[UUID]
) -> list[ProjectionValueRow]:
    return list(
        session.scalars(
            select(ProjectionValueRow).where(
                ProjectionValueRow.snapshot_id == snapshot_id,
                ProjectionValueRow.player_id.in_(player_ids),
            )
        )
    )


def games_between(
    session: Session, season: int, first: date, last: date
) -> list[tuple[UUID, datetime, date, str, str]]:
    """(game ID, tip UTC, game date ET, home, away) for every game between two dates."""
    out = []
    for gid, tip, home, away in session.execute(
        select(NbaGameRow.id, NbaGameRow.tip_utc, NbaGameRow.home_team, NbaGameRow.away_team)
        .where(NbaGameRow.season == season)
        .order_by(NbaGameRow.tip_utc)
    ):
        day = nba_game_date(tip)
        if first <= day <= last:
            out.append((gid, tip, day, home, away))
    return out


def projected_players(session: Session, snapshot_ids: Sequence[UUID]) -> set[UUID]:
    """Players with a row in any of these projection snapshots."""
    if not snapshot_ids:
        return set()
    return set(
        session.scalars(
            select(ProjectionValueRow.player_id)
            .where(ProjectionValueRow.snapshot_id.in_(snapshot_ids))
            .distinct()
        )
    )
