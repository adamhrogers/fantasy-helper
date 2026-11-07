"""Whether a player is expected to play, when the provider has no projection for that game.

A provider's per-game row is the best word on a game (an empty row means "not expected to play").
Without one, the fallback is a per-game season average, and that alone would project an injured
player to play every game. So the latest injury status decides: OUT or SUSPENDED means he sits.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from fantasy_helper.domain.projections import InjuryStatus
from fantasy_helper.domain.stats import Stat
from fantasy_helper.storage.repositories import nba

SITTING = frozenset({InjuryStatus.OUT.value, InjuryStatus.SUSPENDED.value})


def sitting(session: Session, player_ids: Iterable[UUID]) -> set[UUID]:
    """Players whose latest injury status, from any provider, is OUT or SUSPENDED."""
    return {pid for pid, status in nba.latest_injury_status(session, player_ids).items()
            if status in SITTING}  # fmt: skip


def season_fallback(
    season: dict[Stat, float] | None, player_id: UUID, out: set[UUID]
) -> dict[Stat, float] | None:
    """The season average for a game with no per-game row, unless he's out: then he sits ({})."""
    if player_id in out:
        return {}
    return season
