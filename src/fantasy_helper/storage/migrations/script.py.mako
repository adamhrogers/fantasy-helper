"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}
    # Every new table needs: ALTER TABLE "<name>" ENABLE ROW LEVEL SECURITY
    # (no policies), so Supabase's Data API can't read it. tests/integration enforces this.


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
