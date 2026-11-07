"""`fh doctor`: check that this machine (or CI) is set up correctly, one check at a time."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import pool, text

from fantasy_helper.config import Settings
from fantasy_helper.domain.ports import Alert
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.tracing import send_smoke_trace
from fantasy_helper.storage import crypto, migrate
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.raw_store import (
    RawStoreError,
    RawStoreUnavailableError,
    r2_missing_settings,
)


class Check(StrEnum):
    CONFIG = "config"
    ENCRYPTION = "encryption"
    DATABASE = "database"
    TRACING = "tracing"
    ALERTS = "alerts"
    RAW = "raw"


class Status(StrEnum):
    OK = "ok"
    SKIP = "skip"
    FAIL = "fail"


@dataclass(frozen=True)
class Result:
    check: Check
    status: Status
    detail: str


def _first_line(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}".strip()


def check_config(settings: Settings) -> Result:
    return Result(Check.CONFIG, Status.OK, f"settings loaded (env={settings.env})")


def check_encryption(settings: Settings) -> Result:
    if settings.encryption_key is None:
        return Result(
            Check.ENCRYPTION, Status.SKIP, "FH_ENCRYPTION_KEY not set (generate: uv run fh keygen)"
        )
    try:
        key = crypto.load_key(settings.encryption_key.get_secret_value())
        aad = b"fh:doctor"
        if crypto.decrypt(crypto.encrypt(b"ok", key=key, aad=aad), key=key, aad=aad) != b"ok":
            return Result(Check.ENCRYPTION, Status.FAIL, "round trip returned the wrong bytes")
    except crypto.CryptoError as exc:
        return Result(Check.ENCRYPTION, Status.FAIL, str(exc))
    return Result(Check.ENCRYPTION, Status.OK, "AES-256-GCM key loads and round-trips")


def check_database(settings: Settings) -> Result:
    if settings.database_url is None:
        return Result(Check.DATABASE, Status.SKIP, "DATABASE_URL not set (see .env.example)")
    try:
        engine = make_engine(settings.database_url.get_secret_value(), poolclass=pool.NullPool)
    except Exception as exc:
        return Result(Check.DATABASE, Status.FAIL, f"bad DATABASE_URL: {_first_line(exc)}")
    try:
        with engine.connect() as connection:
            version = connection.execute(text("show server_version")).scalar_one()
        current, head = migrate.current_revision(engine), migrate.head_revision()
    except Exception as exc:
        return Result(Check.DATABASE, Status.FAIL, f"can't connect: {_first_line(exc)}")
    finally:
        engine.dispose()
    if current != head:
        return Result(
            Check.DATABASE,
            Status.FAIL,
            f"Postgres {version}, migrations at {current or 'none'} but head is {head}"
            " (run: uv run alembic upgrade head)",
        )
    return Result(Check.DATABASE, Status.OK, f"Postgres {version}, migrations at head ({head})")


def check_tracing(settings: Settings) -> Result:
    if settings.langsmith_api_key is None:
        return Result(Check.TRACING, Status.SKIP, "LANGSMITH_API_KEY not set")
    if not settings.langsmith_tracing:
        return Result(Check.TRACING, Status.SKIP, "LANGSMITH_TRACING is false")
    try:
        run_id = send_smoke_trace(settings)
    except Exception as exc:
        return Result(Check.TRACING, Status.FAIL, _first_line(exc))
    return Result(
        Check.TRACING, Status.OK, f"trace {run_id} is in project {settings.langsmith_project!r}"
    )


def check_alerts(settings: Settings, *, send: bool) -> Result:
    if settings.ntfy_topic is None and settings.env == "prod":
        return Result(
            Check.ALERTS, Status.FAIL, "FH_NTFY_TOPIC not set: prod runs can't reach the phone"
        )
    if settings.ntfy_topic is None:
        return Result(
            Check.ALERTS, Status.SKIP, "FH_NTFY_TOPIC not set (generate: uv run fh keygen --topic)"
        )
    if not send:
        return Result(Check.ALERTS, Status.SKIP, "configured; pass --alert to send a test alert")
    try:
        notifier_from_settings(settings).send(
            Alert(
                title="Fantasy Helper test alert",
                message="fh doctor: ops alerts reach this phone.",
                tags=("white_check_mark",),
            )
        )
    except Exception as exc:
        return Result(Check.ALERTS, Status.FAIL, _first_line(exc))
    return Result(Check.ALERTS, Status.OK, "test alert sent; check your phone")


def check_raw(settings: Settings) -> Result:
    """R2, whenever it's configured: the bucket answers and refuses to overwrite an object."""
    missing = r2_missing_settings(settings)
    if missing and settings.raw_store == "local":
        return Result(
            Check.RAW, Status.SKIP, f"local store at {settings.data_dir / 'raw'}; R2 not configured"
        )
    if missing:
        return Result(Check.RAW, Status.FAIL, f"FH_RAW_STORE=r2 but {', '.join(missing)} not set")
    from fantasy_helper.storage.r2_store import r2_store_from_settings

    try:
        store = r2_store_from_settings(settings)
        store.check_access()
        refused = store.probe_never_overwrites()
    except RawStoreUnavailableError as exc:
        return Result(Check.RAW, Status.FAIL, f"can't reach R2: {exc}")
    except RawStoreError as exc:
        return Result(Check.RAW, Status.FAIL, str(exc))
    if not refused:
        return Result(
            Check.RAW,
            Status.FAIL,
            "R2 accepted an overwrite despite If-None-Match: writes still check first, but "
            "aren't atomic (ADR 0023)",
        )
    using = "in use" if settings.raw_store == "r2" else "configured (FH_RAW_STORE=local)"
    return Result(
        Check.RAW,
        Status.OK,
        f"R2 bucket {settings.r2_bucket!r} {using}; it refuses to overwrite objects",
    )


def run_checks(settings: Settings, *, only: set[Check], send_alert: bool) -> list[Result]:
    checks: dict[Check, Callable[[], Result]] = {
        Check.CONFIG: lambda: check_config(settings),
        Check.ENCRYPTION: lambda: check_encryption(settings),
        Check.DATABASE: lambda: check_database(settings),
        Check.TRACING: lambda: check_tracing(settings),
        Check.ALERTS: lambda: check_alerts(settings, send=send_alert),
        Check.RAW: lambda: check_raw(settings),
    }
    return [run() for check, run in checks.items() if check in only]
