"""The run log: one record per pipeline run (spec: Observability)."""

from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id


class RunKind(StrEnum):
    SNAPSHOT = "snapshot"
    INGEST = "ingest"
    DAILY = "daily"
    WEEKLY = "weekly"
    GRADE = "grade"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"  # shipped, but engine-only
    FAILED = "failed"


class FailureKind(StrEnum):
    """Why one part of a run (a dataset, a league) failed, and whether trying again can help.

    Retrying helps when the cause can clear up in minutes: the network, a source's servers, a
    rate limit, a garbled response, an unreachable database. It can't help when someone has to
    act (expired cookies, a league that's gone) or the code has to change (a new data format, a
    bug): those fail at once instead of retrying.
    """

    NETWORK = "network"
    SERVER_ERROR = "server_error"
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response"
    MISSING_INPUT = "missing_input"
    DATABASE = "database"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    FORMAT_CHANGED = "format_changed"
    STORAGE = "storage"
    STORAGE_DOWN = "storage_down"  # the raw store (R2) couldn't be reached
    BUG = "bug"

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE

    @property
    def describe(self) -> str:
        """Plain words for the phone: no league data, no exception text."""
        return _DESCRIPTIONS[self]


_RETRYABLE = frozenset({
    FailureKind.NETWORK, FailureKind.SERVER_ERROR, FailureKind.RATE_LIMITED,
    FailureKind.BAD_RESPONSE, FailureKind.MISSING_INPUT, FailureKind.DATABASE,
    FailureKind.STORAGE_DOWN,
})  # fmt: skip
_DESCRIPTIONS = {
    FailureKind.NETWORK: "couldn't connect",
    FailureKind.SERVER_ERROR: "the source's servers erred",
    FailureKind.RATE_LIMITED: "rate-limited by the source",
    FailureKind.BAD_RESPONSE: "an unexpected response",
    FailureKind.MISSING_INPUT: "an earlier request it depends on failed",
    FailureKind.DATABASE: "the database was unreachable",
    FailureKind.AUTH: "access denied (expired cookies? re-run fh league add)",
    FailureKind.NOT_FOUND: "not found (league or endpoint gone)",
    FailureKind.FORMAT_CHANGED: "the platform changed its data format (needs a code fix)",
    FailureKind.STORAGE: "couldn't save the file",
    FailureKind.STORAGE_DOWN: "the raw store (R2) was unreachable",
    FailureKind.BUG: "a bug in our code",
}


class SourceFreshness(DomainModel):
    source: str
    pulled_at: UtcDatetime
    rows: int = Field(ge=0)


class RunRecord(DomainModel):
    id: UUID = Field(default_factory=new_id)
    kind: RunKind
    status: RunStatus
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    sources: tuple[SourceFreshness, ...] = ()
    rows_loaded: int = Field(default=0, ge=0)
    model_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    errors: tuple[str, ...] = ()
    shipped_on_time: bool | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("a run can't end before it starts")
        return self
