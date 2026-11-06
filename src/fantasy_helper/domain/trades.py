"""League trades, graded short term and rest of season and graphed over time (P9)."""

from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id


class TradeSide(DomainModel):
    team_external_id: str = Field(min_length=1)
    players_received: tuple[UUID, ...]


class Trade(DomainModel):
    id: UUID = Field(default_factory=new_id)
    league_id: UUID
    external_id: str | None = None
    executed_at: UtcDatetime
    sides: tuple[TradeSide, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        teams = [side.team_external_id for side in self.sides]
        if len(set(teams)) != len(teams):
            raise ValueError("each team appears on one side of a trade")
        players = [p for side in self.sides for p in side.players_received]
        if len(set(players)) != len(players):
            raise ValueError("a player can't be received by two teams in one trade")
        return self
