"""ReportDocument: the contract the phone app renders (spec: Phone delivery).

Short cards, one decision per card, each with the reason behind it.
"""

from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id


class ReportKind(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    SCORECARD = "scorecard"


class CardKind(StrEnum):
    MOVE = "move"
    LOCK_OR_PASS = "lock_or_pass"  # noqa: S105 (lock-or-pass, not a password)
    SIGNAL = "signal"
    RECAP = "recap"
    LEAGUE_MOVES = "league_moves"
    NEWS = "news"
    MATCHUP = "matchup"
    SELF_REVIEW = "self_review"
    REPORT_CARD = "report_card"


class Card(DomainModel):
    id: str = Field(min_length=1)  # stable within a report; feedback refers to it
    kind: CardKind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=1000)
    reason: str | None = None
    recommendation_id: UUID | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)


class ReportDocument(DomainModel):
    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    league_id: UUID
    kind: ReportKind
    for_date: date
    generated_at: UtcDatetime
    cards: tuple[Card, ...]
    engine_only: bool = False  # the brain failed, so this shipped with code-computed numbers only

    @model_validator(mode="after")
    def _unique_card_ids(self) -> Self:
        ids = [card.id for card in self.cards]
        if len(set(ids)) != len(ids):
            raise ValueError("card ids must be unique within a report")
        return self
