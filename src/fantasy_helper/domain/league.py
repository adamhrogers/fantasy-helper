"""Leagues and their settings. The scoring layer turns raw stats into value with these."""

import hashlib
import json
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, computed_field, field_validator, model_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id
from fantasy_helper.domain.player import Position
from fantasy_helper.domain.stats import Stat, StatLine


class Platform(StrEnum):
    ESPN = "espn"
    SLEEPER = "sleeper"
    YAHOO = "yahoo"


class ScoringType(StrEnum):
    POINTS = "points"
    CATEGORIES = "categories"


class LineupMode(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    LOCK_IN = "lock_in"  # Sleeper: each starter counts one game a week, chosen by you


class LeagueFormat(StrEnum):
    """How grades are sliced (spec: registry `league, platform, format`)."""

    POINTS = "points"
    CATEGORIES = "categories"
    LOCK_IN = "lock_in"


class CategoryRule(DomainModel):
    stat: Stat
    higher_is_better: bool = True  # False for turnovers
    ratio_of: tuple[Stat, Stat] | None = None  # FG% is (FGM, FGA)


class ScoringRules(DomainModel):
    scoring_type: ScoringType
    weights: StatLine = Field(default_factory=dict)  # points leagues: fantasy points per stat
    categories: tuple[CategoryRule, ...] = ()  # categories leagues

    @model_validator(mode="after")
    def _matches_scoring_type(self) -> Self:
        if self.scoring_type is ScoringType.POINTS and not self.weights:
            raise ValueError("a points league needs scoring weights")
        if self.scoring_type is ScoringType.CATEGORIES and not self.categories:
            raise ValueError("a categories league needs categories")
        return self


class RosterSlot(DomainModel):
    slot: str = Field(min_length=1)  # "PG", "G", "UTIL", "BE", "IR", …
    count: int = Field(ge=0)
    eligible_positions: tuple[Position, ...] = ()
    starting: bool = True

    @field_validator("eligible_positions")
    @classmethod
    def _sorted(cls, value: tuple[Position, ...]) -> tuple[Position, ...]:
        return tuple(sorted(set(value)))  # deterministic, so the settings hash is stable


class AcquisitionLimits(DomainModel):
    per_matchup: int | None = Field(default=None, ge=0)
    per_season: int | None = Field(default=None, ge=0)


class LeagueSettings(DomainModel):
    """One version of a league's rules. A new version is stored whenever `content_hash` changes,
    so every call can be graded against the settings that existed when it was made."""

    league_id: UUID
    version: int = Field(ge=1)
    effective_from: UtcDatetime
    team_count: int = Field(ge=2)
    scoring: ScoringRules
    lineup_mode: LineupMode
    roster_slots: tuple[RosterSlot, ...]
    acquisition_limits: AcquisitionLimits = AcquisitionLimits()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        payload = self.model_dump(
            mode="json",
            include={"team_count", "scoring", "lineup_mode", "roster_slots", "acquisition_limits"},
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def format(self) -> LeagueFormat:
        if self.lineup_mode is LineupMode.LOCK_IN:
            return LeagueFormat.LOCK_IN
        return LeagueFormat(self.scoring.scoring_type.value)


class League(DomainModel):
    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    platform: Platform
    external_id: str = Field(min_length=1)
    season: int = Field(ge=2000)  # the year the season ends: 2027 for 2026-27 (ESPN's convention)
    name: str
    my_team_external_id: str | None = None
