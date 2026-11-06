"""Box score stats, the raw material every projection and grade is built from."""

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
    MINUTES = "min"


StatLine = dict[Stat, float]
"""Stat values for one game, or per game in a projection."""


class GameLog(DomainModel):
    """What a player actually did in one NBA game."""

    player_id: UUID
    game_id: UUID
    game_date: date
    stats: StatLine
