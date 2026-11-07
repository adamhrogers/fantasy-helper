"""The recommendation registry: every call the app makes, logged before the games.

Records are insert-only. Grades are written separately once the outcome window closes
(spec: Evaluation and grading; docs/architecture.md §8).
"""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id
from fantasy_helper.domain.league import LeagueFormat, Platform

MIN_GRADED_CALLS = 20
"""Below this many graded calls, a report card cell shows Incomplete instead of a grade."""


class CallType(StrEnum):
    ADD_DROP = "add_drop"
    STREAM = "stream"
    LOCK_OR_PASS = "lock_or_pass"  # noqa: S105 (lock-or-pass, not a password)
    START_SIT = "start_sit"
    BUY_LOW = "buy_low"
    PICK_UP_SIGNAL = "pick_up_signal"
    MATCHUP_ODDS = "matchup_odds"


class Action(StrEnum):
    ADD = "add"
    DROP = "drop"
    START = "start"
    SIT = "sit"
    LOCK = "lock"
    PASS = "pass"  # noqa: S105 (lock-in "pass", not a password)
    TRADE_FOR = "trade_for"


class Move(DomainModel):
    action: Action
    player_id: UUID


class Claim(DomainModel):
    """The advice in plain words plus a structured version (players, action, timing)."""

    text: str = Field(min_length=1)
    moves: tuple[Move, ...] = ()
    act_by: UtcDatetime | None = None


class CallInputs(DomainModel):
    """Exactly what the call saw, so it is graded against the same snapshot (spec)."""

    projection_snapshot_ids: tuple[UUID, ...]
    settings_version: int = Field(ge=1)
    take_ids: tuple[UUID, ...] = ()
    fact_sheet_id: UUID | None = None


class Producer(DomainModel):
    agent_setup: str  # e.g. "baseline" or "council-v1"
    models: tuple[str, ...]
    prompt_version: str
    trace_id: str | None = None  # LangSmith


class OutcomeWindow(DomainModel):
    start: UtcDatetime
    end: UtcDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("outcome window must end after it starts")
        return self


class Recommendation(DomainModel):
    id: UUID = Field(default_factory=new_id)
    created_at: UtcDatetime
    league_id: UUID
    user_id: UUID
    platform: Platform
    format: LeagueFormat
    call_type: CallType
    claim: Claim
    expected_value: float  # computed by the engine, in league scoring; never by a model
    confidence: float = Field(ge=0, le=1)
    baseline_call: Claim
    inputs: CallInputs
    producer: Producer
    outcome_window: OutcomeWindow
    first_tip_utc: UtcDatetime  # tip-off of the first game the call depends on
    is_shadow: bool = False  # shadow runs are graded but never shown
    # The code-computed numbers behind the call (scores, value of waiting, games left), so the
    # card and the grade read exactly what the call saw.
    detail: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _logged_before_tip_off(self) -> Self:
        if self.created_at >= self.first_tip_utc:
            raise ValueError("a call must be logged before tip-off, or it can't be graded fairly")
        return self


class MissCause(StrEnum):
    DATA_ERROR = "data_error"  # e.g. stale injury news
    PROJECTION_ERROR = "projection_error"  # the provider was wrong
    REASONING_ERROR = "reasoning_error"  # the brain misread the data
    VARIANCE = "variance"  # right call, bad bounce

    @property
    def fixable(self) -> bool:
        return self is not MissCause.VARIANCE


class MissCauseSource(StrEnum):
    RULE = "rule"
    LLM_JUDGE = "llm_judge"
    HUMAN = "human"


class CallOutcome(DomainModel):
    """The grade for one call, written once its outcome window closes."""

    recommendation_id: UUID
    graded_at: UtcDatetime
    followed: bool | None  # from league transactions and lock actions
    metric_value: float
    baseline_value: float
    miss_cause: MissCause | None = None
    miss_cause_source: MissCauseSource | None = None

    @property
    def lift(self) -> float:
        return self.metric_value - self.baseline_value

    @model_validator(mode="after")
    def _cause_has_source(self) -> Self:
        if (self.miss_cause is None) != (self.miss_cause_source is None):
            raise ValueError("a miss cause and its source are set together")
        return self


class GradeLabel(StrEnum):
    A = "A"  # beats baseline; bootstrap 95% interval above zero
    B = "B"  # beats baseline, not yet with confidence
    C = "C"  # no better than baseline
    D = "D"  # worse than baseline
    INCOMPLETE = "incomplete"  # fewer than MIN_GRADED_CALLS
