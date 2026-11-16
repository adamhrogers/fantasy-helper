"""The `fh` command-line interface. Commands are added milestone by milestone."""

import secrets
from collections.abc import Callable
from typing import Annotated

import typer
from sqlalchemy import pool
from sqlalchemy.orm import Session

from fantasy_helper import __version__
from fantasy_helper.adapters._http import make_client
from fantasy_helper.adapters.espn.public import GAME_LOG_DEPTH
from fantasy_helper.cli import fixtures, leagues, raw, report, run
from fantasy_helper.cli._common import REPORTED_EXIT, alert_crash, fail, watched
from fantasy_helper.cli._runs import start_next
from fantasy_helper.cli.doctor import Check, Status, run_checks
from fantasy_helper.cli.ingest import ingest as ingest_command
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.events import events_from_settings
from fantasy_helper.observability.healthchecks import heartbeat_from_settings
from fantasy_helper.observability.logs import configure_logging, get_logger
from fantasy_helper.pipeline.freshness import mark_snapshot_complete
from fantasy_helper.pipeline.morning import incompleteness
from fantasy_helper.pipeline.snapshot import DATASETS, SnapshotResult, SnapshotSet, run_snapshot
from fantasy_helper.storage import migrate as migrations
from fantasy_helper.storage.crypto import generate_key
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.raw_store import (
    RawStore,
    RawStoreError,
    RawStoreUnavailableError,
    raw_store_from_settings,
)
from fantasy_helper.storage.repositories.runs import record_run

log = get_logger(__name__)

app = typer.Typer(
    name="fh",
    help="Fantasy Helper operator CLI.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals can hold secrets
)

app.add_typer(leagues.app, name="league")
app.add_typer(fixtures.app, name="fixtures")
app.add_typer(run.app, name="run")
app.add_typer(raw.app, name="raw")
app.add_typer(report.app, name="report")
app.command("ingest")(ingest_command)

_SYMBOLS = {Status.OK: "✓", Status.SKIP: "-", Status.FAIL: "✗"}


@app.callback()
def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command("migrate")
def migrate_command() -> None:
    """Bring the database to the latest schema. The Cloud Run `migrate` job runs this on every
    deploy, before the jobs that use the new tables (ADR 0027). Safe to repeat."""
    settings = get_settings()
    if settings.database_url is None:
        raise fail("DATABASE_URL is not set (see .env.example)")
    url = settings.database_url.get_secret_value()
    engine = make_engine(url, poolclass=pool.NullPool)
    try:
        before = migrations.current_revision(engine)
    finally:
        engine.dispose()
    migrations.upgrade(url)
    head = migrations.head_revision()
    if before == head:
        typer.echo(f"already at head ({head}): nothing to do")
    else:
        typer.echo(f"migrated {before or 'an empty database'} to {head}")


@app.command()
def keygen(
    topic: Annotated[
        bool, typer.Option("--topic", help="Generate a private ntfy topic name instead.")
    ] = False,
) -> None:
    """Print a new FH_ENCRYPTION_KEY, or an FH_NTFY_TOPIC with --topic. One per environment."""
    typer.echo(f"fh-{secrets.token_urlsafe(24)}" if topic else generate_key())


@app.command()
def doctor(
    only: Annotated[
        list[Check] | None, typer.Option("--only", help="Run only these checks.")
    ] = None,
    alert: Annotated[
        bool, typer.Option("--alert", help="Also send a test ops alert to your phone.")
    ] = False,
) -> None:
    """Check config, encryption, database, LangSmith tracing and ops alerts."""
    results = run_checks(get_settings(), only=set(only or Check), send_alert=alert)
    for result in results:
        typer.echo(f"{_SYMBOLS[result.status]} {result.check.value:<11} {result.detail}")
    if any(result.status is Status.FAIL for result in results):
        raise typer.Exit(code=1)


@app.command()
def snapshot(
    run_set: Annotated[
        SnapshotSet | None,
        typer.Option(
            "--set",
            help="morning (default): everything; evening: pre-tip injuries and projections.",
        ),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option(
            "--only", help="Capture only these datasets (e.g. espn.game_logs), to backfill."
        ),
    ] = None,
    game_log_depth: Annotated[
        int,
        typer.Option(help="Box scores: each player's last N team games (30 covers the season)."),
    ] = GAME_LOG_DEPTH,
) -> None:
    """Save today's raw projections and injury statuses. The scheduler runs this twice a day."""
    unknown = set(only or ()) - {d.label for d in DATASETS}
    if unknown:
        typer.echo(f"✗ unknown dataset {', '.join(sorted(unknown))}", err=True)
        raise typer.Exit(code=2)
    run_set = run_set or SnapshotSet.MORNING
    # A settings error stops here with exit 1, before any alert channel or ping key is known. On
    # GitHub the workflow alerts for it; on Cloud Run there's no workflow, so only the watchdog
    # does, once its grace passes. Every failure after this point is sent to the phone and the
    # watchdog.
    settings = get_settings()
    label = f"{run_set.value.title()} snapshot"
    with watched(heartbeat_from_settings(settings, f"snapshot-{run_set.value}")):
        try:
            store = raw_store_from_settings(settings)
        except RawStoreError as exc:
            typer.echo(f"✗ {exc}", err=True)
            what = "the raw store isn't configured"
            raise alert_crash(settings, RunKind.SNAPSHOT, label, what) from exc
        store_problem = _check_store(store)
        record = _run_log(settings)
        try:
            with make_client() as client:
                result = run_snapshot(
                    run_set,
                    client=client,
                    store=store,
                    notifier=notifier_from_settings(settings),
                    record=record,
                    only=frozenset(only) if only else None,
                    game_log_depth=game_log_depth,
                    progress=settings.notify_progress,
                )
        except Exception as exc:
            log.exception("snapshot_crashed", error=type(exc).__name__)
            raise alert_crash(
                settings, RunKind.SNAPSHOT, label, f"an unexpected {type(exc).__name__}"
            ) from exc
        for r in result.results:
            if r.reused:
                detail = "already saved today"
            else:
                detail = f"{r.items:>5} items {r.bytes / 1024:>6.0f} KB" if r.key else ""
            typer.echo(f"{'✓' if r.ok else '✗'} {r.label:<28} {detail}  {r.error or ''}".rstrip())
        ok = sum(1 for r in result.results if r.ok)
        if record is None:
            logged = "run not logged (no DATABASE_URL)"
        else:
            logged = "run logged" if result.recorded else "run NOT logged"
        typer.echo(
            f"{result.run.status.value}: {ok}/{len(result.results)} datasets saved, {logged}"
        )
        if store_problem is not None:
            typer.echo(f"✗ raw store check: {store_problem}", err=True)
            events_from_settings(settings, RunKind.SNAPSHOT, label=label).failed(
                f"{store_problem}. The snapshot ran anyway, but its files may not be where ingest "
                "looks. Check FH_R2_ENDPOINT and the R2 keys (docs/runbooks/cloud-snapshots.md).",
                title=f"{label}: raw store check failed",
                urgent=True,
            )
            raise typer.Exit(code=REPORTED_EXIT)
        if run_set is SnapshotSet.MORNING and not only:  # a partial backfill completes nothing
            _hand_over(settings, store, result, label)
        if result.run.status is not RunStatus.SUCCEEDED or (
            record is not None and not result.recorded
        ):
            raise typer.Exit(code=REPORTED_EXIT)  # the run's own events already said why


def _hand_over(settings: Settings, store: RawStore, result: SnapshotResult, label: str) -> None:
    """The morning snapshot is the first link of the chain (ADR 0028). If every dataset saved and
    passed, record that (the daily and weekly runs insist on it) and start the daily run. If not,
    nothing downstream runs: the snapshot's own alert already says what failed, and the daily
    run's late-report check says the report isn't coming."""
    events = events_from_settings(settings, RunKind.SNAPSHOT, label=label)
    why_not = incompleteness(result, store)
    if why_not is not None:
        typer.echo(f"✗ the morning set is incomplete ({why_not}): no completion marker")
        if settings.next_job is not None:
            events.step("Daily report not started", f"The snapshot is incomplete: {why_not}.")
        return
    try:
        mark_snapshot_complete(
            store,
            run_set=result.run_set.value,
            datasets=[r.label for r in result.results],
            at=SystemClock().now(),
        )
    except (RawStoreError, OSError) as exc:
        events.failed(
            "The snapshot saved everything, but its completion marker couldn't be written, so "
            "the daily report was not started. Check the raw store.",
            title=f"{label}: couldn't record completion",
            urgent=True,
        )
        typer.echo(f"✗ completion marker not written ({type(exc).__name__})", err=True)
        raise typer.Exit(code=REPORTED_EXIT) from exc
    typer.echo("✓ the morning set is complete")
    start_next(settings, events, after=label)


def _check_store(store: RawStore) -> str | None:
    """A misconfigured store (wrong endpoint, bad keys) can still accept writes in the wrong
    place, so it's checked up front. The snapshot runs either way: a day can't be recovered.
    An outage isn't a misconfiguration; the run's own retries handle it."""
    try:
        store.check_access()
    except RawStoreUnavailableError:
        return None
    except RawStoreError as exc:
        return str(exc)
    return None


def _run_log(settings: Settings) -> Callable[[RunRecord], None] | None:
    if settings.database_url is None:
        return None
    url = settings.database_url.get_secret_value()

    def record(run: RunRecord) -> None:
        engine = make_engine(url, poolclass=pool.NullPool)
        try:
            with Session(engine) as session, session.begin():
                record_run(session, run)
        finally:
            engine.dispose()

    return record
