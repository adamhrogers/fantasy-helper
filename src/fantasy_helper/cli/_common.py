"""Helpers shared by `fh` command groups that touch the database."""

from collections.abc import Iterator
from contextlib import contextmanager

import typer
from sqlalchemy import pool
from sqlalchemy.orm import Session

from fantasy_helper.config import Settings
from fantasy_helper.storage import crypto
from fantasy_helper.storage.db import make_engine


def fail(message: str) -> typer.Exit:
    typer.echo(f"✗ {message}", err=True)
    return typer.Exit(code=1)


def require_db_and_key(settings: Settings) -> tuple[str, bytes]:
    if settings.database_url is None:
        raise fail("DATABASE_URL is not set (see .env.example)")
    if settings.encryption_key is None:
        raise fail("FH_ENCRYPTION_KEY is not set (generate one: uv run fh keygen)")
    try:
        key = crypto.load_key(settings.encryption_key.get_secret_value())
    except crypto.CryptoError as exc:
        raise fail(f"FH_ENCRYPTION_KEY is invalid: {exc}") from exc
    return settings.database_url.get_secret_value(), key


@contextmanager
def transaction(url: str) -> Iterator[Session]:
    engine = make_engine(url, poolclass=pool.NullPool)
    try:
        with Session(engine) as session, session.begin():
            yield session
    finally:
        engine.dispose()
