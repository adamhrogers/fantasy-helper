"""ReportDocument: the contract the phone app renders (spec: Phone delivery).

Short cards, one decision per card, each with the reason behind it. A card that lists several
players or facts gives each its own line in `items`, which the app draws as bullets under a
short `body`.
"""

from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, JsonValue, StringConstraints, model_validator

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


MAX_ITEMS = 30
MAX_ITEM_LENGTH = 240

Item = Annotated[str, StringConstraints(min_length=1, max_length=MAX_ITEM_LENGTH)]


class Card(DomainModel):
    id: str = Field(min_length=1)  # stable within a report; feedback refers to it
    kind: CardKind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(max_length=1000)  # a short summary above the items, or the whole card
    # One line per player or fact, drawn as bullets. Empty for a card that is one statement, and
    # in every report saved before the field existed (2026-11-22).
    items: tuple[Item, ...] = Field(default=(), max_length=MAX_ITEMS)
    reason: str | None = None
    recommendation_id: UUID | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)


def bullets(lines: Iterable[str]) -> tuple[str, ...]:
    """Lines for `Card.items`: blanks dropped, each and the count kept within the card's limits,
    so a long night can shorten a card but never fail a report."""
    kept = [line.strip() for line in lines if line.strip()]
    return tuple(
        line if len(line) <= MAX_ITEM_LENGTH else line[: MAX_ITEM_LENGTH - 1] + "\u2026"
        for line in kept[:MAX_ITEMS]
    )


class GameDay(DomainModel):
    """The NBA schedule for one date, the same for every league: how many games, and when the
    first tips. The phone shows it once at the top of a day's reports."""

    day: date
    games: int = Field(ge=0)
    first_tip_utc: UtcDatetime | None = None

    @model_validator(mode="after")
    def _a_tip_exactly_when_there_are_games(self) -> Self:
        if (self.games > 0) != (self.first_tip_utc is not None):
            raise ValueError("a first tip-off is given exactly when there are games")
        return self


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
