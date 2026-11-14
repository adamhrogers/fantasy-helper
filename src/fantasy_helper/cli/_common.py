"""Helpers shared by `fh` command groups: exits, alerts, the watchdog, the database."""

from collections.abc import Iterator
from contextlib import contextmanager

import typer
from sqlalchemy import pool
from sqlalchemy.orm import Session

from fantasy_helper.config import Settings
from fantasy_helper.domain.runs import RunKind
from fantasy_helper.observability.events import events_from_settings
from fantasy_helper.observability.healthchecks import Heartbeat
from fantasy_helper.storage import crypto
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.raw_store import RawStore, RawStoreError, raw_store_from_settings

# Exit code for a failure the app has already sent to the phone. A scheduled workflow alerts on
# any other failure, including a timeout, so nothing fails silently (run-events runbook).
REPORTED_EXIT = 3


@contextmanager
def watched(heartbeat: Heartbeat) -> Iterator[None]:
    """Pings the missed-run watchdog around a scheduled run (ADR 0025): `start`, then `success`
    on a clean exit and `fail` on any other, reported or not (observability/healthchecks.py
    says why). Heartbeats never raise, so the watchdog can't stop the run it watches."""
    heartbeat.start()
    try:
        yield
    except typer.Exit as exc:
        if exc.exit_code == 0:
            heartbeat.success()
        elif exc.exit_code == REPORTED_EXIT:
            heartbeat.fail("finished with a problem it reported to the phone")
        else:
            heartbeat.fail(f"exited with code {exc.exit_code}")
        raise
    except BaseException as exc:
        heartbeat.fail(f"stopped on an unexpected {type(exc).__name__}")
        raise
    heartbeat.success()


def alert_crash(settings: Settings, kind: RunKind, label: str, what: str) -> typer.Exit:
    """An urgent alert for a run that stopped outside its own run events, and the exit to raise.
    Names the kind of failure only, never an exception's text."""
    events_from_settings(settings, kind, label=label).failed(
        f"It stopped on {what}. Nothing after that ran; see the run's log.",
        title=f"{label} crashed",
        urgent=True,
    )
    return typer.Exit(code=REPORTED_EXIT)


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


def open_raw_store(settings: Settings) -> RawStore:
    """The raw store FH_RAW_STORE names (ADR 0023), or a clean CLI failure if it's misconfigured."""
    try:
        return raw_store_from_settings(settings)
    except RawStoreError as exc:
        raise fail(str(exc)) from exc


@contextmanager
def transaction(url: str) -> Iterator[Session]:
    engine = make_engine(url, poolclass=pool.NullPool)
    try:
        with Session(engine) as session, session.begin():
            yield session
    finally:
        engine.dispose()
