"""Box score upserts leave unchanged rows alone (they were bloating the table), and still apply
a correction when a stat changes."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from fantasy_helper.domain.player import IdSource
from fantasy_helper.domain.records import (
    GameLogRecord,
    PlayerKey,
    ScheduledGameRecord,
)
from fantasy_helper.domain.stats import Stat
from fantasy_helper.storage.repositories import nba
from fantasy_helper.storage.tables import PlayerGameStatRow, PlayerRow

pytestmark = pytest.mark.db

DAY = date(2026, 11, 14)


@pytest.fixture
def session(engine: Engine):  # type: ignore[no-untyped-def]
    with Session(engine) as session, session.begin():
        yield session
        session.rollback()


def record(points: float, *, rebounds: float | None = 5.0) -> GameLogRecord:
    stats = {Stat.POINTS: points}
    if rebounds is not None:
        stats[Stat.REBOUNDS] = rebounds
    return GameLogRecord(
        player=PlayerKey(source=IdSource.ESPN, external_id="1"),
        external_game_id="g1",
        season=2027,
        scoring_period=26,
        team="BOS",
        played=True,
        stats=stats,
    )


def load(session: Session, rec: GameLogRecord, player: UUID, game: UUID) -> int:
    return nba.upsert_game_logs(session, [(rec, player, game, DAY)])


def version(session: Session, player: UUID) -> str:
    """Where this row version sits on disk (ctid). Rewriting a row makes a new version at a new
    place, even inside one transaction, where xmin would stay the same."""
    return str(
        session.execute(
            text("select ctid::text from player_game_stats where player_id = :p"), {"p": player}
        ).scalar_one()
    )


def test_an_identical_reload_writes_nothing_and_a_change_is_applied(session: Session) -> None:
    player = uuid4()
    session.add(PlayerRow(id=player, full_name="P"))
    nba.upsert_games(
        session,
        2027,
        [
            ScheduledGameRecord(
                external_id="g1",
                scoring_period=26,
                tip_utc=datetime(2026, 11, 14, 0, 30, tzinfo=UTC),
                home_team="BOS",
                away_team="NYK",
            )
        ],
    )
    game = nba.game_index(session, 2027)["g1"][0]

    assert load(session, record(30.0), player, game) == 1  # new row
    first = version(session, player)

    assert load(session, record(30.0), player, game) == 0  # same values: untouched
    assert version(session, player) == first

    assert load(session, record(32.0), player, game) == 1  # a stat correction wins
    assert version(session, player) != first
    session.expire_all()  # the upsert ran in SQL; drop what the session cached
    row = session.scalars(select(PlayerGameStatRow)).one()
    assert (row.pts, row.reb) == (32.0, 5.0)

    assert load(session, record(32.0, rebounds=None), player, game) == 1  # a stat went missing
    session.expire_all()
    assert session.scalars(select(PlayerGameStatRow)).one().reb is None
    assert load(session, record(32.0, rebounds=None), player, game) == 0  # NULL against NULL
