"""m3 reports: the daily and weekly reports the scheduled runs build (ADR 0026)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15 08:19:09.177955
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("for_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("engine_only", sa.Boolean(), nullable=False),
        sa.Column("cards", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_reports_league_id_leagues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_reports_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint(
            "league_id", "kind", "for_date", name=op.f("uq_reports_league_id_kind_for_date")
        ),
    )
    op.create_index(
        "ix_reports_user_id_kind_for_date", "reports", ["user_id", "kind", "for_date"], unique=False
    )
    # Supabase's Data API must not read it: row-level security on, no policies (CLAUDE.md).
    op.execute(sa.text('ALTER TABLE "reports" ENABLE ROW LEVEL SECURITY'))


def downgrade() -> None:
    op.drop_index("ix_reports_user_id_kind_for_date", table_name="reports")
    op.drop_table("reports")
