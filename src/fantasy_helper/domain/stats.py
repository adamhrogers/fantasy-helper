"""Box score stats, the raw material every projection and grade is built from.

There is one box score per player and game, shared by every league (ADR 0022). Leagues differ
only in how they weight these stats, so this list covers everything a league's points scoring
can weight that a box score records.
"""

from datetime import date
from enum import StrEnum
from uuid import UUID

from fantasy_helper.domain._base import DomainModel


class Stat(StrEnum):
    POINTS = "pts"
    REBOUNDS = "reb"
    OFFENSIVE_REBOUNDS = "oreb"
    DEFENSIVE_REBOUNDS = "dreb"
    ASSISTS = "ast"
    STEALS = "stl"
    BLOCKS = "blk"
    TURNOVERS = "to"
    THREES_MADE = "3pm"
    THREES_ATTEMPTED = "3pa"
    FIELD_GOALS_MADE = "fgm"
    FIELD_GOALS_ATTEMPTED = "fga"
    FREE_THROWS_MADE = "ftm"
    FREE_THROWS_ATTEMPTED = "fta"
    PERSONAL_FOULS = "pf"
    DOUBLE_DOUBLES = "dd"
    TRIPLE_DOUBLES = "td"
    QUADRUPLE_DOUBLES = "qd"
    TECHNICAL_FOULS = "tf"
    FLAGRANT_FOULS = "ff"
    EJECTIONS = "ej"
    DISQUALIFICATIONS = "dq"
    MINUTES = "min"


# Scoring settings some leagues use that aren't box score stats themselves but are exact
# combinations of them. A weight on one becomes weights on its parts: a league scoring
# "field goals missed" at -1 scores FGA at -1 and FGM at +1, the same for every game.
FIELD_GOALS_MISSED: dict[Stat, float] = {
    Stat.FIELD_GOALS_ATTEMPTED: 1.0,
    Stat.FIELD_GOALS_MADE: -1.0,
}
FREE_THROWS_MISSED: dict[Stat, float] = {
    Stat.FREE_THROWS_ATTEMPTED: 1.0,
    Stat.FREE_THROWS_MADE: -1.0,
}
THREES_MISSED: dict[Stat, float] = {Stat.THREES_ATTEMPTED: 1.0, Stat.THREES_MADE: -1.0}


def add_weight(weights: dict[Stat, float], parts: dict[Stat, float], points: float) -> None:
    """Add `points` per unit of a stat that is the sum of `parts` (stat → coefficient)."""
    for stat, coefficient in parts.items():
        weights[stat] = weights.get(stat, 0.0) + coefficient * points


StatLine = dict[Stat, float]
"""Stat values for one game, or per game in a projection."""


class GameLog(DomainModel):
    """What a player actually did in one NBA game."""

    player_id: UUID
    game_id: UUID
    game_date: date
    stats: StatLine
