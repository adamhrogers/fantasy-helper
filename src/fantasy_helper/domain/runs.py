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
