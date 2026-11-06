"""Ports: the interfaces the rest of the system depends on, implemented by outer layers.

The data-source ports (LeaguePlatform, ProjectionSource, NewsSource, StatsSource) are added
with the M1 adapters, shaped by what the data spike finds rather than guessed now.
"""

from datetime import UTC, datetime
from enum import IntEnum
from typing import Protocol

from pydantic import Field

from fantasy_helper.domain._base import DomainModel


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class AlertPriority(IntEnum):
    """ntfy's priority scale."""

    LOW = 2
    DEFAULT = 3
    HIGH = 4
    URGENT = 5


class Alert(DomainModel):
    """An ops alert for the operator. Never put secrets or league data in one."""

    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=2000)
    priority: AlertPriority = AlertPriority.DEFAULT
    tags: tuple[str, ...] = ()


class Notifier(Protocol):
    def send(self, alert: Alert) -> None: ...
