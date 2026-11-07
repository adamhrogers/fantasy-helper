"""m2 box scores, typed stat columns, read-path indexes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06 18:12:12.875057

- `player_game_stats` (box scores, one row per player and game) and `raw_loads`.
- `projection_rows.per_game` JSONB becomes one REAL column per stat (ADR 0021); existing rows
  are converted in place, and the downgrade converts them back.
- Composite indexes for the read paths (ADR 0020).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB key (the domain Stat value at 0002) -> column. Frozen here: migrations never import
# application code, which keeps changing.
STAT_COLUMNS = {
    "pts": "pts", "reb": "reb", "oreb": "oreb", "dreb": "dreb", "ast": "ast", "stl": "stl",
    "blk": "blk", "to": "tov", "3pm": "fg3m", "3pa": "fg3a", "fgm": "fgm", "fga": "fga",
    "ftm": "ftm", "fta": "fta", "pf": "pf", "dd": "dd", "td": "td", "min": "minutes",
}  # fmt: skip


# Box score stats added in M2 that no projection JSONB carried.
NEW_STAT_COLUMNS = ("qd", "tf", "ff", "ej", "dq")


def _stat_columns() -> list[sa.Column[float]]:
    return [
        sa.Column(column, sa.REAL(), nullable=True)
        for column in (*STAT_COLUMNS.values(), *NEW_STAT_COLUMNS)
    ]


def upgrade() -> None:
    op.create_table(
        "raw_loads",
        sa.Column("raw_key", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("raw_key", name=op.f("pk_raw_loads")),
    )
    op.create_table(
        "player_game_stats",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("scoring_period", sa.Integer(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("played", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_stat_columns(),
        sa.ForeignKeyConstraint(
            ["game_id"], ["nba_games.id"], name=op.f("fk_player_game_stats_game_id_nba_games")
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_player_game_stats_player_id_players")
        ),
        sa.PrimaryKeyConstraint("player_id", "game_id", name=op.f("pk_player_game_stats")),
    )
    op.create_index(op.f("ix_player_game_stats_game_date"), "player_game_stats", ["game_date"])
    op.create_index(
        "ix_player_game_stats_player_id_game_date", "player_game_stats", ["player_id", "game_date"]
    )
    for table in ("raw_loads", "player_game_stats"):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))

    # projection_rows: JSONB -> typed columns, converting the rows already loaded.
    for column in _stat_columns():
        op.add_column("projection_rows", column)
    op.execute(
        sa.text(
            "UPDATE projection_rows SET "  # noqa: S608 (constant column names, no input)
            + ", ".join(
                f"{column} = (per_game ->> '{key}')::real" for key, column in STAT_COLUMNS.items()
            )
        )
    )
    op.drop_column("projection_rows", "per_game")
    op.drop_index(op.f("ix_projection_rows_snapshot_id"), table_name="projection_rows")
    op.create_index(
        "ix_projection_rows_snapshot_id_player_id", "projection_rows", ["snapshot_id", "player_id"]
    )

    op.create_index(
        "ix_projection_snapshots_provider_scope_pulled_at",
        "projection_snapshots",
        ["provider", "scope", "pulled_at"],
    )
    op.drop_index(op.f("ix_injury_observations_player_id"), table_name="injury_observations")
    op.create_index(
        "ix_injury_observations_player_id_provider_observed_at",
        "injury_observations",
        ["player_id", "provider", "observed_at"],
    )
    op.drop_index(op.f("ix_transactions_proposed_at"), table_name="transactions")
    op.create_index(
        "ix_transactions_league_id_proposed_at", "transactions", ["league_id", "proposed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_league_id_proposed_at", table_name="transactions")
    op.create_index(op.f("ix_transactions_proposed_at"), "transactions", ["proposed_at"])
    op.drop_index(
        "ix_injury_observations_player_id_provider_observed_at", table_name="injury_observations"
    )
    op.create_index(op.f("ix_injury_observations_player_id"), "injury_observations", ["player_id"])
    op.drop_index(
        "ix_projection_snapshots_provider_scope_pulled_at", table_name="projection_snapshots"
    )

    op.drop_index("ix_projection_rows_snapshot_id_player_id", table_name="projection_rows")
    op.create_index(op.f("ix_projection_rows_snapshot_id"), "projection_rows", ["snapshot_id"])
    op.add_column(
        "projection_rows",
        sa.Column("per_game", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    pairs = ", ".join(f"'{key}', {column}" for key, column in STAT_COLUMNS.items())
    op.execute(
        sa.text(
            "UPDATE projection_rows SET per_game = "  # noqa: S608 (constant column names, no input)
            f"jsonb_strip_nulls(jsonb_build_object({pairs}))"
        )
    )
    op.alter_column("projection_rows", "per_game", nullable=False)
    for column in (*STAT_COLUMNS.values(), *NEW_STAT_COLUMNS):
        op.drop_column("projection_rows", column)

    op.drop_index("ix_player_game_stats_player_id_game_date", table_name="player_game_stats")
    op.drop_index(op.f("ix_player_game_stats_game_date"), table_name="player_game_stats")
    op.drop_table("player_game_stats")
    op.drop_table("raw_loads")
