"""Database tables (SQLAlchemy 2.0).

M0: users, leagues, encrypted platform credentials and the run log.
M1: players and the identity crosswalk, the NBA schedule, versioned league settings, teams,
daily rosters, matchups, transactions, projections and injury status changes.
M2: box scores, typed stat columns and read-path indexes (ADRs 0020, 0021).

Every table has row-level security enabled in its migration, with no policies. Our backend
connects as the table owner, which bypasses RLS, while Supabase's public Data API sees nothing.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
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

from fantasy_helper.domain.stats import Stat, StatLine

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


# One REAL column per stat, not a JSONB blob: about half the bytes per row (ADR 0021), and
# typed. Column names avoid SQL keywords ("to") and leading digits ("3pm").
STAT_COLUMNS: dict[Stat, str] = {
    Stat.POINTS: "pts",
    Stat.REBOUNDS: "reb",
    Stat.OFFENSIVE_REBOUNDS: "oreb",
    Stat.DEFENSIVE_REBOUNDS: "dreb",
    Stat.ASSISTS: "ast",
    Stat.STEALS: "stl",
    Stat.BLOCKS: "blk",
    Stat.TURNOVERS: "tov",
    Stat.THREES_MADE: "fg3m",
    Stat.THREES_ATTEMPTED: "fg3a",
    Stat.FIELD_GOALS_MADE: "fgm",
    Stat.FIELD_GOALS_ATTEMPTED: "fga",
    Stat.FREE_THROWS_MADE: "ftm",
    Stat.FREE_THROWS_ATTEMPTED: "fta",
    Stat.PERSONAL_FOULS: "pf",
    Stat.DOUBLE_DOUBLES: "dd",
    Stat.TRIPLE_DOUBLES: "td",
    Stat.QUADRUPLE_DOUBLES: "qd",
    Stat.TECHNICAL_FOULS: "tf",
    Stat.FLAGRANT_FOULS: "ff",
    Stat.EJECTIONS: "ej",
    Stat.DISQUALIFICATIONS: "dq",
    Stat.MINUTES: "minutes",
}
if set(STAT_COLUMNS) != set(Stat):  # a new Stat needs a column (and a migration)
    raise RuntimeError("STAT_COLUMNS is missing a Stat")


class StatColumns:
    """Mixin: a nullable REAL per stat. NULL means the source didn't give that stat."""

    pts: Mapped[float | None] = mapped_column(REAL)
    reb: Mapped[float | None] = mapped_column(REAL)
    oreb: Mapped[float | None] = mapped_column(REAL)
    dreb: Mapped[float | None] = mapped_column(REAL)
    ast: Mapped[float | None] = mapped_column(REAL)
    stl: Mapped[float | None] = mapped_column(REAL)
    blk: Mapped[float | None] = mapped_column(REAL)
    tov: Mapped[float | None] = mapped_column(REAL)
    fg3m: Mapped[float | None] = mapped_column(REAL)
    fg3a: Mapped[float | None] = mapped_column(REAL)
    fgm: Mapped[float | None] = mapped_column(REAL)
    fga: Mapped[float | None] = mapped_column(REAL)
    ftm: Mapped[float | None] = mapped_column(REAL)
    fta: Mapped[float | None] = mapped_column(REAL)
    pf: Mapped[float | None] = mapped_column(REAL)
    dd: Mapped[float | None] = mapped_column(REAL)
    td: Mapped[float | None] = mapped_column(REAL)
    qd: Mapped[float | None] = mapped_column(REAL)
    tf: Mapped[float | None] = mapped_column(REAL)
    ff: Mapped[float | None] = mapped_column(REAL)
    ej: Mapped[float | None] = mapped_column(REAL)
    dq: Mapped[float | None] = mapped_column(REAL)
    minutes: Mapped[float | None] = mapped_column(REAL)


def stat_values(line: StatLine) -> dict[str, float]:
    """A StatLine as column values for an insert."""
    return {STAT_COLUMNS[stat]: value for stat, value in line.items()}


def stat_line(row: Any) -> StatLine:
    """The non-NULL stat columns of a row (ORM object or Core row) as a StatLine."""
    out: StatLine = {}
    for stat, column in STAT_COLUMNS.items():
        value = getattr(row, column)
        if value is not None:
            out[stat] = float(value)
    return out


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
    __table_args__ = (
        UniqueConstraint("league_id", "external_id"),
        Index("ix_transactions_league_id_proposed_at", "league_id", "proposed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    team_external_id: Mapped[str | None] = mapped_column(Text)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    __table_args__ = (
        # "The latest pull of each provider and scope" is an index lookup (ADR 0020).
        Index("ix_projection_snapshots_provider_scope_pulled_at", "provider", "scope", "pulled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    season: Mapped[int] = mapped_column(Integer)
    period: Mapped[str | None] = mapped_column(Text)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_key: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = _created_at()


class ProjectionValueRow(StatColumns, Base):
    """One player's per-game projection in one snapshot (or for one game, with game_date)."""

    __tablename__ = "projection_rows"
    __table_args__ = (
        # "These players in that pull" stays an index lookup however much history piles up.
        Index("ix_projection_rows_snapshot_id_player_id", "snapshot_id", "player_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projection_snapshots.id", ondelete="CASCADE")
    )
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True)
    game_date: Mapped[date | None] = mapped_column(Date)
    games: Mapped[float | None] = mapped_column(Float)


class InjuryObservationRow(Base):
    """Injury status changes only: a row is written when a player's status (or note) differs
    from the last one recorded for that provider. These become timeline-graph markers."""

    __tablename__ = "injury_observations"
    __table_args__ = (
        Index(
            "ix_injury_observations_player_id_provider_observed_at",
            "player_id", "provider", "observed_at",
        ),
    )  # fmt: skip

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    provider: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_key: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------------------ M2: box scores


class PlayerGameStatRow(StatColumns, Base):
    """One player's line in one NBA game (a box score row). A game he sat out is a row with
    `played` false and no stats, so availability is data too. Stat corrections overwrite."""

    __tablename__ = "player_game_stats"
    __table_args__ = (Index("ix_player_game_stats_player_id_game_date", "player_id", "game_date"),)

    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), primary_key=True)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_games.id"), primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    game_date: Mapped[date] = mapped_column(Date, index=True)  # the tip's local date (ET)
    scoring_period: Mapped[int] = mapped_column(Integer)
    team: Mapped[str | None] = mapped_column(Text)
    played: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RawLoadRow(Base):
    """Raw payloads already loaded into tables that don't keep their own raw key, so ingest
    loads each payload once (box scores today)."""

    __tablename__ = "raw_loads"

    raw_key: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset: Mapped[str] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = _created_at()


# ------------------------------------------------------------------------- M2: the registry


class RecommendationRow(Base):
    """Every call the app makes, logged before tip-off and never edited (ADR 0005). A database
    trigger rejects UPDATE and DELETE; grades go in their own table (M6). `dedupe_key` makes
    logging the same call twice (a rerun) a no-op, never an edit."""

    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("league_id", "dedupe_key"),
        Index("ix_recommendations_league_id_created_at", "league_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    platform: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    call_type: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    claim: Mapped[dict[str, Any]] = mapped_column(JSONB)
    baseline_call: Mapped[dict[str, Any]] = mapped_column(JSONB)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    producer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected_value: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    outcome_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_tip_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_shadow: Mapped[bool] = mapped_column(Boolean)
