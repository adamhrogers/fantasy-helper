"""NBA players, identified once and mapped to every platform's ID."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from fantasy_helper.domain._base import DomainModel, new_id


class Position(StrEnum):
    PG = "PG"
    SG = "SG"
    SF = "SF"
    PF = "PF"
    C = "C"


class IdSource(StrEnum):
    ESPN = "espn"
    SLEEPER = "sleeper"
    YAHOO = "yahoo"
    NBA = "nba"


class ExternalId(DomainModel):
    source: IdSource
    value: str = Field(min_length=1)


class Player(DomainModel):
    """One canonical player. The identity crosswalk (M1) links each platform's ID to this one."""

    id: UUID = Field(default_factory=new_id)
    full_name: str = Field(min_length=1)
    nba_team: str | None = None
    positions: tuple[Position, ...] = ()
    external_ids: tuple[ExternalId, ...] = ()

    def external_id(self, source: IdSource) -> str | None:
        return next((x.value for x in self.external_ids if x.source == source), None)
