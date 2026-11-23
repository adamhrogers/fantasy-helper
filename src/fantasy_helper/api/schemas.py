"""What the API sends and accepts. The reports are the domain's own `ReportDocument`, so the
phone renders exactly what the jobs saved."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fantasy_helper.domain.reports import GameDay, ReportDocument


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(Strict):
    status: Literal["ok"]
    version: str


class LeagueOut(Strict):
    id: UUID
    name: str
    platform: str


class Me(Strict):
    name: str
    timezone: str
    leagues: list[LeagueOut]


class ReportsOut(Strict):
    for_date: date | None  # None when nothing is saved yet
    reports: list[ReportDocument]
    # Daily reports only: the NBA games on `for_date` (the same for every league, so the app
    # shows it once). None for weekly reports and when nothing is saved.
    games: GameDay | None = None


class PushKey(Strict):
    public_key: str


class PushKeys(Strict):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=256)


class SubscriptionIn(Strict):
    """The shape a browser's `PushSubscription.toJSON()` gives."""

    endpoint: str = Field(min_length=12, max_length=2048, pattern=r"^https://")
    keys: PushKeys


class SubscriptionOut(Strict):
    endpoint: str


class SubscriptionRemove(Strict):
    endpoint: str = Field(min_length=12, max_length=2048)
