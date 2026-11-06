from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from fantasy_helper.domain.league import League, Platform
from fantasy_helper.storage.tables import LeagueRow


def _to_domain(row: LeagueRow) -> League:
    return League(
        id=row.id,
        user_id=row.user_id,
        platform=Platform(row.platform),
        external_id=row.external_id,
        season=row.season,
        name=row.name,
        my_team_external_id=row.my_team_external_id,
    )


def upsert(session: Session, league: League) -> tuple[League, bool]:
    """Insert, or refresh the name and team of a league already added. Returns (league, created)."""
    row = session.scalar(
        select(LeagueRow).where(
            LeagueRow.user_id == league.user_id,
            LeagueRow.platform == league.platform.value,
            LeagueRow.external_id == league.external_id,
            LeagueRow.season == league.season,
        )
    )
    if row is None:
        row = LeagueRow(
            id=league.id,
            user_id=league.user_id,
            platform=league.platform.value,
            external_id=league.external_id,
            season=league.season,
            name=league.name,
            my_team_external_id=league.my_team_external_id,
        )
        session.add(row)
        session.flush()
        return _to_domain(row), True
    row.name = league.name
    row.my_team_external_id = league.my_team_external_id
    session.flush()
    return _to_domain(row), False


def list_for_user(session: Session, user_id: UUID) -> list[League]:
    rows = session.scalars(
        select(LeagueRow)
        .where(LeagueRow.user_id == user_id)
        .order_by(LeagueRow.platform, LeagueRow.name)
    )
    return [_to_domain(row) for row in rows]
