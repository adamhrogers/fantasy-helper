"""Provider projections and injury status, kept as immutable daily snapshots.

Grades and timeline graphs use the snapshot that existed at the time, so nothing here is ever
updated in place (docs/architecture.md §5.2). A day that isn't snapshotted can't be recovered.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id
from fantasy_helper.domain.stats import StatLine


class Provider(StrEnum):
    """Where projections and injury news come from. Free sources first (spec)."""

    ESPN = "espn"
    SLEEPER = "sleeper"


class ProjectionScope(StrEnum):
    SEASON = "season"
    REST_OF_SEASON = "rest_of_season"
    WEEK = "week"
    DAY = "day"


class ProjectionSnapshot(DomainModel):
    """One pull of one provider's projections at one moment."""

    id: UUID = Field(default_factory=new_id)
    provider: Provider
    scope: ProjectionScope
    season: int
    period: str | None = None  # matchup week or ISO date, depending on scope
    pulled_at: UtcDatetime


class ProjectionRow(DomainModel):
    """Per-stat projection for one player. Code multiplies it by league weights (spec)."""

    snapshot_id: UUID
    player_id: UUID
    games: float | None = Field(default=None, ge=0)
    per_game: StatLine


class InjuryStatus(StrEnum):
    ACTIVE = "active"
    DAY_TO_DAY = "day_to_day"
    OUT = "out"
    SUSPENDED = "suspended"


class InjuryObservation(DomainModel):
    player_id: UUID
    provider: Provider
    status: InjuryStatus
    observed_at: UtcDatetime
    note: str | None = None
