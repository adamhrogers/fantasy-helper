"""Which projections were in force at a moment, and the per-game stat line they give a player.

The weekly overview reads the newest snapshots; the daily recap reads the ones from before
last night's first tip-off, so "what was expected" is what was known before the games.

Per game, the provider's projection for that game when there is one (Sleeper's per-game rows,
RotoWire's numbers), then a per-game season average (Sleeper's, then ESPN's); a game the provider
left empty means "not expected to play".
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.domain.projections import ProjectionScope, Provider
from fantasy_helper.domain.stats import Stat
from fantasy_helper.facts.availability import season_fallback
from fantasy_helper.storage.repositories import nba
from fantasy_helper.storage.tables import stat_line


def snapshots_as_of(session: Session, week: int, as_of: datetime) -> dict[str, UUID | None]:
    return {
        "sleeper_game": nba.latest_projection_snapshot(
            session, Provider.SLEEPER, ProjectionScope.WEEK, str(week), as_of
        ),
        "sleeper_season": nba.latest_projection_snapshot(
            session, Provider.SLEEPER, ProjectionScope.SEASON, None, as_of
        ),
        "espn_season": nba.latest_projection_snapshot(
            session, Provider.ESPN, ProjectionScope.SEASON, None, as_of
        ),
    }


class ProjectionBook:
    """Per-game stat lines, best source first."""

    def __init__(
        self,
        session: Session,
        snapshots: dict[str, UUID | None],
        ids: Sequence[UUID],
        out: set[UUID],
    ) -> None:
        self.out = out
        self.by_game: dict[UUID, dict[date, dict[Stat, float]]] = defaultdict(dict)
        self.season: dict[UUID, dict[Stat, float]] = {}
        if snapshots["sleeper_game"]:
            for row in nba.projection_rows(session, snapshots["sleeper_game"], ids):
                if row.game_date is not None:
                    # An empty per-game row (0 games) means "not expected to play".
                    self.by_game[row.player_id][row.game_date] = (
                        {} if row.games == 0 else stat_line(row)
                    )
        for key in ("espn_season", "sleeper_season"):  # Sleeper overwrites ESPN where both exist
            if snapshots[key]:
                for row in nba.projection_rows(session, snapshots[key], ids):  # type: ignore[arg-type]
                    self.season[row.player_id] = stat_line(row)

    def for_game(self, player_id: UUID, day: date) -> dict[Stat, float] | None:
        if day in self.by_game.get(player_id, {}):
            return self.by_game[player_id][day]
        return season_fallback(self.season.get(player_id), player_id, self.out)
