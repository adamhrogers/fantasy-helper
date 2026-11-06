"""Database engine and sessions. Supabase Postgres in every environment (ADR 0006)."""

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_url(url: str) -> str:
    """Accept the plain `postgresql://` URL Supabase shows and use the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def make_engine(url: str, **kwargs: Any) -> Engine:
    # Supabase's transaction pooler can't use prepared statements, so psycopg never prepares.
    return create_engine(
        normalize_url(url),
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
        **kwargs,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
