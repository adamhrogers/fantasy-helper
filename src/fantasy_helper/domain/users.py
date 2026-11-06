"""Users and their platform logins. Logins are stored encrypted (docs/architecture.md §12.1)."""

from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator

from fantasy_helper.domain._base import DomainModel, UtcDatetime, new_id
from fantasy_helper.domain.league import Platform


class Role(StrEnum):
    OWNER = "owner"
    BETA = "beta"


class User(DomainModel):
    id: UUID = Field(default_factory=new_id)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=1)
    timezone: str  # IANA name, e.g. "America/New_York"; reports are delivered by local time
    role: Role = Role.BETA

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value!r}") from exc
        return value


class PlatformCredential(DomainModel):
    """One user's login for one platform, as stored: ciphertext only."""

    id: UUID = Field(default_factory=new_id)
    user_id: UUID
    platform: Platform
    ciphertext: bytes
    key_version: int = Field(default=1, ge=1)
    created_at: UtcDatetime


class EspnCredentials(DomainModel):
    """ESPN's two login cookies, in memory only. SecretStr keeps them out of reprs and logs."""

    espn_s2: SecretStr
    swid: SecretStr
