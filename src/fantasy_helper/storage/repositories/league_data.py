"""Per-league data: settings versions, teams, daily rosters, matchups and transactions."""

import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fantasy_helper.domain.records import (
    MatchupRecord,
    RosterEntryRecord,
    RulesRecord,
    TeamRecord,
    TransactionRecord,
)
from fantasy_helper.storage.tables import (
    LeagueSettingsVersionRow,
    MatchupRow,
    RosterEntryRow,
    TeamRow,
    TransactionRow,
)


def save_rules(
    session: Session, league_id: UUID, record: RulesRecord, at: datetime
) -> tuple[int, bool]:
    """Store a new settings version only when the rules changed. Returns (version, created)."""
    latest = session.execute(
        select(LeagueSettingsVersionRow.version, LeagueSettingsVersionRow.content_hash)
        .where(LeagueSettingsVersionRow.league_id == league_id)
        .order_by(LeagueSettingsVersionRow.version.desc())
        .limit(1)
    ).first()
    content_hash = record.rules.content_hash
    if latest is not None and latest.content_hash == content_hash:
        return latest.version, False
    version = (latest.version if latest else 0) + 1
    session.add(
        LeagueSettingsVersionRow(
            id=uuid.uuid4(),
            league_id=league_id,
            version=version,
            content_hash=content_hash,
            rules=record.rules.model_dump(mode="json"),
            unmapped_scoring=list(record.unmapped_scoring),
            effective_from=at,
        )
    )
    session.flush()
    return version, True


def upsert_teams(session: Session, league_id: UUID, teams: list[TeamRecord]) -> int:
    if not teams:
        return 0
    stmt = pg_insert(TeamRow).values(
        [{"id": uuid.uuid4(), "league_id": league_id, "external_id": t.external_id, "name": t.name}
         for t in teams]
    )  # fmt: skip
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["league_id", "external_id"],
            set_={"name": stmt.excluded.name, "updated_at": func.now()},
        )
    )
    return len(teams)


def replace_roster(
    session: Session, league_id: UUID, as_of: date, entries: list[tuple[RosterEntryRecord, UUID]]
) -> int:
    """Today's roster snapshot for a league: re-running the same day replaces it."""
    session.execute(
        delete(RosterEntryRow).where(
            RosterEntryRow.league_id == league_id, RosterEntryRow.as_of == as_of
        )
    )
    if entries:
        session.execute(
            insert(RosterEntryRow),
            [
                {
                    "id": uuid.uuid4(), "league_id": league_id, "as_of": as_of,
                    "team_external_id": e.team_external_id, "player_id": player_id, "slot": e.slot,
                    "acquired_at": e.acquired_at, "acquisition_type": e.acquisition_type,
                }
                for e, player_id in entries
            ],
        )  # fmt: skip
    return len(entries)


def upsert_matchups(session: Session, league_id: UUID, matchups: list[MatchupRecord]) -> int:
    if not matchups:
        return 0
    rows = [
        {
            "id": uuid.uuid4(),
            "league_id": league_id,
            "external_id": m.external_id,
            "period": m.period,
            "home_team": m.home.team_external_id,
            "away_team": m.away.team_external_id if m.away else None,
            "home_points": m.home.total_points,
            "away_points": m.away.total_points if m.away else None,
            "winner": m.winner,
            "detail": {
                side: {
                    "points_by_period": {str(k): v for k, v in s.points_by_period.items()},
                    "player_points": s.player_points,
                }
                for side, s in (("home", m.home), ("away", m.away))
                if s is not None
            },
        }
        for m in matchups
    ]
    stmt = pg_insert(MatchupRow).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["league_id", "external_id"],
            set_={
                col: stmt.excluded[col]
                for col in (
                    "period",
                    "home_team",
                    "away_team",
                    "home_points",
                    "away_points",
                    "winner",
                    "detail",
                )
            }
            | {"updated_at": func.now()},
        )
    )
    return len(rows)


def upsert_transactions(session: Session, league_id: UUID, records: list[TransactionRecord]) -> int:
    """Statuses change over time (a pending trade executes), so existing rows are updated."""
    if not records:
        return 0
    rows = {
        r.external_id: {
            "id": uuid.uuid4(),
            "league_id": league_id,
            "external_id": r.external_id,
            "kind": r.kind.value,
            "status": r.status.value,
            "team_external_id": r.team_external_id,
            "proposed_at": r.proposed_at,
            "scoring_period": r.scoring_period,
            "items": [
                {
                    "source": i.player.source.value,
                    "external_id": i.player.external_id,
                    "action": i.action.value,
                    "from_team": i.from_team,
                    "to_team": i.to_team,
                }
                for i in r.items
            ],
        }
        for r in records
    }
    stmt = pg_insert(TransactionRow).values(list(rows.values()))
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["league_id", "external_id"],
            set_={
                col: stmt.excluded[col]
                for col in (
                    "kind",
                    "status",
                    "team_external_id",
                    "proposed_at",
                    "scoring_period",
                    "items",
                )
            }
            | {"updated_at": func.now()},
        )
    )
    return len(rows)
