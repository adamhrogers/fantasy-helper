"""Database tables (SQLAlchemy 2.0).

M0: users, leagues, encrypted platform credentials and the run log.
M1: players and the identity crosswalk, the NBA schedule, versioned league settings, teams,
daily rosters, matchups, transactions, projections and injury status changes.

Every table has row-level security enabled in its migration, with no policies. Our backend
connects as the table owner, which bypasses RLS, while Supabase's public Data API sees nothing.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class LeagueRow(Base):
    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("user_id", "platform", "external_id", "season"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    season: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    my_team_external_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class PlatformCredentialRow(Base):
    """One user's login for one platform: ciphertext from storage.crypto, never plaintext."""

    __tablename__ = "platform_credentials"
    __table_args__ = (UniqueConstraint("user_id", "platform"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    rows_loaded: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    model_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), server_default=text("0"))
    errors: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    shipped_on_time: Mapped[bool | None] = mapped_column(Boolean)


# ----------------------------------------------------------------------------- M1: players


class PlayerRow(Base):
    """One canonical NBA player. Display fields come from ESPN when it knows the player."""

    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    nba_team: Mapped[str | None] = mapped_column(Text)
    positions: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlayerExternalIdRow(Base):
    """The crosswalk: one platform ID to one canonical player, and how the link was made."""

    __tablename__ = "player_external_ids"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, primary_key=True)
    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True
    )
    # primary | name_team | name_unique | override | unmatched
    match_method: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class NbaGameRow(Base):
    __tablename__ = "nba_games"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    espn_game_id: Mapped[str] = mapped_column(Text, unique=True)
    scoring_period: Mapped[int] = mapped_column(Integer, index=True)
    tip_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_team: Mapped[str] = mapped_column(Text)
    away_team: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ------------------------------------------------------------------------- M1: league data


class LeagueSettingsVersionRow(Base):
    """A league's rules, versioned: a new row only when the rules' content hash changes."""

    __tablename__ = "league_settings_versions"
    __table_args__ = (UniqueConstraint("league_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    unmapped_scoring: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TeamRow(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("league_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RosterEntryRow(Base):
    """Who was on which team on a given day: one snapshot per league per day."""

    __tablename__ = "roster_entries"
    __table_args__ = (UniqueConstraint("league_id", "as_of", "player_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    team_external_id: Mapped[str] = mapped_column(Text)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True)
    slot: Mapped[str] = mapped_column(Text)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquisition_type: Mapped[str | None] = mapped_column(Text)


class MatchupRow(Base):
    __tablename__ = "matchups"
    __table_args__ = (UniqueConstraint("league_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(Text)
    period: Mapped[int] = mapped_column(Integer)
    home_team: Mapped[str] = mapped_column(Text)
    away_team: Mapped[str | None] = mapped_column(Text)
    home_points: Mapped[float | None] = mapped_column(Float)
    away_points: Mapped[float | None] = mapped_column(Float)
    winner: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)  # points by day / per player, per side
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TransactionRow(Base):
    """Items keep each platform's own player IDs; identity resolves them when read, so a
    corrected crosswalk link never means rewriting transaction history."""

    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("league_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    team_external_id: Mapped[str | None] = mapped_column(Text)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scoring_period: Mapped[int | None] = mapped_column(Integer)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ------------------------------------------------------------- M1: projections and injuries


class ProjectionSnapshotRow(Base):
    """One provider's projections as pulled at one moment. `raw_key` points at the raw
    payload it was loaded from, and makes loading the same snapshot twice a no-op."""

    __tablename__ = "projection_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    season: Mapped[int] = mapped_column(Integer)
    period: Mapped[str | None] = mapped_column(Text)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_key: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()


class ProjectionValueRow(Base):
    __tablename__ = "projection_rows"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projection_snapshots.id", ondelete="CASCADE"), index=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True)
    game_date: Mapped[date | None] = mapped_column(Date)
    games: Mapped[float | None] = mapped_column(Float)
    per_game: Mapped[dict[str, float]] = mapped_column(JSONB)


class InjuryObservationRow(Base):
    """Injury status changes only: a row is written when a player's status (or note) differs
    from the last one recorded for that provider. These become timeline-graph markers."""

    __tablename__ = "injury_observations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True)
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_key: Mapped[str | None] = mapped_column(Text)
