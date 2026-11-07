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
    """One scoring category: a counting stat (PTS) or a ratio of two (FG% is FGM / FGA)."""

    name: str = Field(min_length=1)  # as shown to the user: "PTS", "FG%"
    stat: Stat | None = None
    ratio_of: tuple[Stat, Stat] | None = None
    higher_is_better: bool = True  # False for turnovers

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.stat is None) == (self.ratio_of is None):
            raise ValueError("a category is either one stat or a ratio of two")
        return self


class ScoringBonus(DomainModel):
    """A league setting: extra points when one game's stat reaches a threshold. Whether a league
    has any, and their thresholds and points, come from its own settings (a Sleeper league's
    `bonus_reb_20p: 1.5` is `ScoringBonus(stat=REBOUNDS, threshold=20, points=1.5)`)."""

    stat: Stat
    threshold: float
    points: float


class ScoringRules(DomainModel):
    scoring_type: ScoringType
    weights: StatLine = Field(default_factory=dict)  # points leagues: fantasy points per stat
    categories: tuple[CategoryRule, ...] = ()  # categories leagues
    bonuses: tuple[ScoringBonus, ...] = ()

    @model_validator(mode="after")
    def _matches_scoring_type(self) -> Self:
        if self.scoring_type is ScoringType.POINTS and not self.weights:
            raise ValueError("a points league needs scoring weights")
        if self.scoring_type is ScoringType.CATEGORIES and not self.categories:
            raise ValueError("a categories league needs categories")
        return self


# Canonical slot names every adapter maps into.
SLOT_POSITIONS: dict[str, tuple[Position, ...]] = {
    "PG": (Position.PG,),
    "SG": (Position.SG,),
    "SF": (Position.SF,),
    "PF": (Position.PF,),
    "C": (Position.C,),
    "G": (Position.PG, Position.SG),
    "F": (Position.SF, Position.PF),
    "SG/SF": (Position.SG, Position.SF),
    "G/F": (Position.PG, Position.SG, Position.SF, Position.PF),
    "PF/C": (Position.PF, Position.C),
    "F/C": (Position.SF, Position.PF, Position.C),
    "UTIL": tuple(Position),
    "BENCH": tuple(Position),
    "IR": tuple(Position),
}
NON_STARTING_SLOTS = frozenset({"BENCH", "IR"})


class RosterSlot(DomainModel):
    slot: str = Field(min_length=1)  # one of SLOT_POSITIONS
    count: int = Field(ge=0)
    eligible_positions: tuple[Position, ...] = ()
    starting: bool = True

    @field_validator("eligible_positions")
    @classmethod
    def _sorted(cls, value: tuple[Position, ...]) -> tuple[Position, ...]:
        return tuple(sorted(set(value)))  # deterministic, so the settings hash is stable

    @classmethod
    def named(cls, slot: str, count: int) -> "RosterSlot":
        if slot not in SLOT_POSITIONS:
            raise ValueError(f"unknown roster slot {slot!r}")
        return cls(
            slot=slot,
            count=count,
            eligible_positions=SLOT_POSITIONS[slot],
            starting=slot not in NON_STARTING_SLOTS,
        )


class AcquisitionLimits(DomainModel):
    per_season: int | None = Field(default=None, ge=0)  # None means unlimited
    per_matchup: int | None = Field(default=None, ge=0)
    # ESPN can scale the matchup limit by the matchup's length: 0.571 a day is 4 per 7-day week.
    per_matchup_per_day: float | None = Field(default=None, ge=0)


class LeagueRules(DomainModel):
    """Everything about a league that changes how a call is valued."""

    team_count: int = Field(ge=2)
    scoring: ScoringRules
    lineup_mode: LineupMode
    roster_slots: tuple[RosterSlot, ...]
    acquisition_limits: AcquisitionLimits = AcquisitionLimits()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def format(self) -> LeagueFormat:
        if self.lineup_mode is LineupMode.LOCK_IN:
            return LeagueFormat.LOCK_IN
        return LeagueFormat(self.scoring.scoring_type.value)


class LeagueSettings(DomainModel):
    """One version of a league's rules. A new version is stored whenever the rules' hash
    changes, so every call is graded against the settings that existed when it was made."""

    league_id: UUID
    version: int = Field(ge=1)
    effective_from: UtcDatetime
    rules: LeagueRules

    @property
    def content_hash(self) -> str:
        return self.rules.content_hash

    @property
    def format(self) -> LeagueFormat:
        return self.rules.format


class League(DomainModel):
    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    platform: Platform
    external_id: str = Field(min_length=1)
    season: int = Field(ge=2000)  # the year the season ends: 2027 for 2026-27 (ESPN's convention)
    name: str
    my_team_external_id: str | None = None
