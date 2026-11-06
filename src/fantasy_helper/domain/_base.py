"""Shared building blocks for domain models."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware; everything is stored in UTC")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
"""A timezone-aware datetime, normalized to UTC. Naive datetimes are rejected."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> UUID:
    return uuid4()


class DomainModel(BaseModel):
    """Immutable and strict: unknown fields are errors and instances can't be changed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
