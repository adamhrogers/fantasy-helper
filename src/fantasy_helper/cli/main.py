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
from fantasy_helper.cli import fixtures, leagues, raw, run
from fantasy_helper.cli._common import fail
from fantasy_helper.cli.doctor import Check, Status, run_checks
from fantasy_helper.cli.ingest import ingest as ingest_command
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.domain.ports import Alert, AlertPriority, Notifier, SystemClock
from fantasy_helper.domain.runs import RunRecord, RunStatus
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.logs import configure_logging, get_logger
from fantasy_helper.pipeline.cron import UnknownCronError, job_for
from fantasy_helper.pipeline.snapshot import DATASETS, SnapshotSet, run_snapshot
from fantasy_helper.storage.crypto import generate_key
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.raw_store import RawStoreError, raw_store_from_settings
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


_SNAPSHOT_JOBS = {"snapshot-morning": SnapshotSet.MORNING, "snapshot-evening": SnapshotSet.EVENING}


@app.command()
def snapshot(
    run_set: Annotated[
        SnapshotSet | None,
        typer.Option(
            "--set",
            help="morning (default): everything; evening: pre-tip injuries and projections.",
        ),
    ] = None,
    cron: Annotated[
        str | None,
        typer.Option(
            "--cron",
            help="The GitHub cron entry that started this run. It picks the set, or exits "
            "quietly if the entry is for the other UTC offset today (pipeline/cron.py).",
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
    if cron is not None:
        if run_set is not None:
            typer.echo("✗ use --set or --cron, not both", err=True)
            raise typer.Exit(code=2)
        try:
            job = job_for(cron, SystemClock().now())
        except UnknownCronError as exc:
            typer.echo(f"✗ {exc}", err=True)
            raise typer.Exit(code=2) from exc
        if job is None:
            typer.echo(f"skipped: cron {cron!r} is the other UTC offset's entry today")
            return
        if job not in _SNAPSHOT_JOBS:
            typer.echo(f"✗ cron {cron!r} is for {job}, not a snapshot", err=True)
            raise typer.Exit(code=2)
        run_set = _SNAPSHOT_JOBS[job]
    run_set = run_set or SnapshotSet.MORNING
    settings = get_settings()
    notifier = notifier_from_settings(settings)
    label = f"{run_set.value.title()} snapshot"
    try:
        store = raw_store_from_settings(settings)
    except RawStoreError as exc:
        _alert_crash(notifier, label, "the raw store isn't configured")
        raise fail(str(exc)) from exc
    record = _run_log(settings)
    try:
        with make_client() as client:
            result = run_snapshot(
                run_set,
                client=client,
                store=store,
                notifier=notifier,
                record=record,
                only=frozenset(only) if only else None,
                game_log_depth=game_log_depth,
                progress=settings.notify_progress,
            )
    except Exception as exc:
        _alert_crash(notifier, label, f"an unexpected {type(exc).__name__}")
        raise
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
    typer.echo(f"{result.run.status.value}: {ok}/{len(result.results)} datasets saved, {logged}")
    if result.run.status is not RunStatus.SUCCEEDED or (record is not None and not result.recorded):
        raise typer.Exit(code=1)


def _alert_crash(notifier: Notifier, label: str, what: str) -> None:
    """A loud alert for a run that stopped before or outside its own run events. Names the
    kind of failure only, never the exception's text. Never raises."""
    try:
        notifier.send(
            Alert(
                title=f"{label} crashed",
                message=f"It stopped on {what}. Nothing after that ran; see the run's log.",
                priority=AlertPriority.URGENT,
                tags=("rotating_light",),
            )
        )
    except Exception as exc:
        log.error("crash_alert_failed", error=type(exc).__name__)


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
