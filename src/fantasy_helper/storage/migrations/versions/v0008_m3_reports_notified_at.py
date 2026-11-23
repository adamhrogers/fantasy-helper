"""m3 push: when a report's push notification was sent (ADR 0030)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive and nullable (ADR 0027): the report jobs still on the old image never write it, and
    # the reports already saved simply count as not notified, which is true.
    op.add_column("reports", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "notified_at")
