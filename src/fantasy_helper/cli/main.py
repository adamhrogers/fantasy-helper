"""The `fh` command-line interface. Commands are added milestone by milestone."""

import secrets
from collections.abc import Callable
from typing import Annotated

import typer
from sqlalchemy import pool
from sqlalchemy.orm import Session

from fantasy_helper import __version__
from fantasy_helper.adapters._http import make_client
from fantasy_helper.cli import leagues
from fantasy_helper.cli.doctor import Check, Status, run_checks
from fantasy_helper.config import Settings, get_settings
from fantasy_helper.domain.runs import RunRecord, RunStatus
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.observability.logs import configure_logging
from fantasy_helper.pipeline.snapshot import SnapshotSet, run_snapshot
from fantasy_helper.storage.crypto import generate_key
from fantasy_helper.storage.db import make_engine
from fantasy_helper.storage.raw_store import LocalRawStore
from fantasy_helper.storage.repositories.runs import record_run

app = typer.Typer(
    name="fh",
    help="Fantasy Helper operator CLI.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals can hold secrets
)

app.add_typer(leagues.app, name="league")

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


@app.command()
def snapshot(
    run_set: Annotated[
        SnapshotSet,
        typer.Option(
            "--set", help="morning: everything; evening: pre-tip injuries and projections."
        ),
    ] = SnapshotSet.MORNING,
) -> None:
    """Save today's raw projections and injury statuses. The scheduler runs this twice a day."""
    settings = get_settings()
    with make_client() as client:
        result = run_snapshot(
            run_set,
            client=client,
            store=LocalRawStore(settings.data_dir / "raw"),
            notifier=notifier_from_settings(settings),
            record=_run_log(settings),
        )
    for r in result.results:
        detail = f"{r.items:>5} items {r.bytes / 1024:>6.0f} KB" if r.key else ""
        typer.echo(f"{'✓' if r.ok else '✗'} {r.label:<28} {detail}  {r.error or ''}".rstrip())
    ok = sum(1 for r in result.results if r.ok)
    logged = "run logged" if result.recorded else "run NOT logged"
    typer.echo(f"{result.run.status.value}: {ok}/{len(result.results)} datasets saved, {logged}")
    if result.run.status is not RunStatus.SUCCEEDED or not result.recorded:
        raise typer.Exit(code=1)


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
