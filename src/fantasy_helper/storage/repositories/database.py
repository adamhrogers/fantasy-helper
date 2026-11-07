"""Facts about the database itself."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def size_bytes(session: Session) -> int:
    """What Supabase counts against the plan's database-size quota (ADR 0021)."""
    return int(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
