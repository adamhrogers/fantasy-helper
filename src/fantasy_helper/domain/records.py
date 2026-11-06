"""Platform records: league data keyed by each platform's own IDs, before identity resolution.

Adapters produce these from raw responses. Ingest then maps each `PlayerKey` to a canonical
player through the identity crosswalk. Adapters never import the identity layer, so mapping a
response stays a pure function of the response (docs/architecture.md §3).
"""

from enum import StrEnum

from pydantic import Field

from fantasy_helper.domain._base import DomainModel, UtcDatetime
from fantasy_helper.domain.league import LeagueRules
from fantasy_helper.domain.player import IdSource


class RulesRecord(DomainModel):
    rules: LeagueRules
    # Scoring settings with a non-zero value that no Stat maps to. Ingest alerts on these
    # rather than silently scoring the league wrong.
    unmapped_scoring: tuple[str, ...] = ()


class PlayerKey(DomainModel):
    source: IdSource
    external_id: str = Field(min_length=1)


class TeamRecord(DomainModel):
    external_id: str
    name: str  # a leaguemate's team name: private league data, never in fixtures or logs


class RosterEntryRecord(DomainModel):
    team_external_id: str
    player: PlayerKey
    slot: str  # a canonical slot name (domain.league.SLOT_POSITIONS)
    acquired_at: UtcDatetime | None = None
    acquisition_type: str | None = None  # "ADD", "DRAFT", "TRADE", … as the platform says


class MatchupSide(DomainModel):
    team_external_id: str
    total_points: float | None = None
    points_by_period: dict[int, float] = Field(default_factory=dict)  # ESPN: per scoring day
    player_points: dict[str, float] = Field(default_factory=dict)  # Sleeper: per player, the week


class MatchupRecord(DomainModel):
    external_id: str
    period: int  # ESPN matchup period or Sleeper week
    home: MatchupSide
    away: MatchupSide | None = None  # None for a bye
    winner: str | None = None  # "home", "away", "tie", or None while undecided


class TransactionKind(StrEnum):
    FREE_AGENT = "free_agent"
    WAIVER = "waiver"
    TRADE = "trade"
    COMMISSIONER = "commissioner"
    OTHER = "other"


class TransactionStatus(StrEnum):
    EXECUTED = "executed"
    PROPOSED = "proposed"
    PENDING = "pending"
    DECLINED = "declined"
    VETOED = "vetoed"
    CANCELED = "canceled"
    FAILED = "failed"


class ItemAction(StrEnum):
    ADD = "add"
    DROP = "drop"
    TRADE = "trade"


class TransactionItem(DomainModel):
    player: PlayerKey
    action: ItemAction
    from_team: str | None = None  # None means free agency or waivers
    to_team: str | None = None


class TransactionRecord(DomainModel):
    external_id: str
    kind: TransactionKind
    status: TransactionStatus
    team_external_id: str | None = None  # who initiated it
    proposed_at: UtcDatetime | None = None
    scoring_period: int | None = None  # ESPN scoring day or Sleeper week
    items: tuple[TransactionItem, ...] = ()

    @property
    def is_completed_trade(self) -> bool:
        return self.kind is TransactionKind.TRADE and self.status is TransactionStatus.EXECUTED
