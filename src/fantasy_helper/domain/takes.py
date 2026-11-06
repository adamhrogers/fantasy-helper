"""Your watchlist and takes: beliefs about players, dated and graded over time.

Each take has a direction and a horizon so code can grade it without an LLM (decision D7).
"""

from datetime import datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id


class Direction(StrEnum):
    BEAT = "beat"  # will outperform projections
    TRAIL = "trail"  # will underperform projections

    @property
    def sign(self) -> int:
        return 1 if self is Direction.BEAT else -1


class Horizon(IntEnum):
    DAYS_7 = 7
    DAYS_14 = 14
    DAYS_30 = 30


Conviction = Annotated[int, Field(ge=1, le=5)]


class WatchlistEntry(DomainModel):
    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    player_id: UUID
    added_at: UtcDatetime
    active: bool = True


class Take(DomainModel):
    """A dated, immutable belief. Editing a take creates a new one that supersedes it."""

    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    player_id: UUID
    created_at: UtcDatetime
    direction: Direction
    horizon: Horizon
    conviction: Conviction
    text: str = Field(min_length=1, max_length=280)
    supersedes_id: UUID | None = None

    @property
    def window_end(self) -> datetime:
        return self.created_at + timedelta(days=int(self.horizon))
