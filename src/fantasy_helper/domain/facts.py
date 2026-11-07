"""The FactSheet: the compact, code-computed facts about one league that models read (ADR 0004).

Every number in it comes from the engine. It's stored (append-only, hashed) as the replay unit
for evals and shadow runs, and read with one lookup (ADR 0020). v1 (M2) is the weekly sheet.
"""

import hashlib
import json
from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import Field, computed_field

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id

TOKEN_BUDGET = 12_000  # a FactSheet must fit a prompt comfortably (tested)


class SheetKind(StrEnum):
    WEEKLY = "weekly"


class FactPlayer(DomainModel):
    player_id: UUID
    name: str
    nba_team: str | None = None
    slot: str  # roster slot, or "FA"
    games: int  # games left this week
    started: int  # of those, games in a starting slot
    per_game: float  # expected points a game, in this league's scoring
    value_over_replacement: float | None = None


class FactMove(DomainModel):
    add: UUID
    add_name: str
    drop: UUID
    drop_name: str
    gain: float  # expected points added to this week's started lineup
    games_added: int


class FactDay(DomainModel):
    day: date
    games: int  # my players with a game that day
    started: int  # of those, how many fit a starting slot
    slots: int  # starting slots


class FactMatchup(DomainModel):
    opponent: str
    my_points: float
    my_sd: float
    their_points: float
    their_sd: float
    # Mid-week: points already banked this week (in my_points / their_points too), from our
    # box scores and each finished day's lineup. None for a whole-week projection.
    my_banked: float | None = None
    their_banked: float | None = None
    # None when the score so far is unknown (a lock-in league mid-week, or a missing lineup):
    # a win probability from the rest of the week alone would mislead.
    win_probability: float | None = Field(default=None, ge=0, le=1)


class FactSheet(DomainModel):
    id: UUID = Field(default_factory=new_id)
    league_id: UUID
    kind: SheetKind
    week: int
    from_day: date
    as_of: UtcDatetime
    settings_version: int
    projection_snapshot_ids: tuple[UUID, ...] = ()
    points_league: bool
    games: int
    startable: int
    days: tuple[FactDay, ...] = ()
    matchup: FactMatchup | None = None
    my_players: tuple[FactPlayer, ...]
    free_agents: tuple[FactPlayer, ...] = ()  # the best few, for context
    moves: tuple[FactMove, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Same facts, same hash, whenever and wherever they were built."""
        payload = self.model_dump(mode="json", exclude={"id", "as_of", "content_hash"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def approx_tokens(self) -> int:
        """About 4 characters a token."""
        return len(self.model_dump_json()) // 4
