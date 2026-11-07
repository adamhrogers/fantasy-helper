"""`fh run daily`: the morning run (ingest, lock-or-pass calls, report). Weekly comes later."""

import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from fantasy_helper.adapters._http import make_client
from fantasy_helper.cli._common import fail, require_db_and_key, transaction
from fantasy_helper.config import get_settings
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.events import RunEvents, events_from_settings
from fantasy_helper.pipeline.daily import DailyResult, daily_summary, run_daily
from fantasy_helper.pipeline.ingest import MissingSnapshotError
from fantasy_helper.pipeline.retry import RunStoppedError, outcome, run_with_retries
from fantasy_helper.reports.html import render
from fantasy_helper.storage.raw_store import LocalRawStore
from fantasy_helper.storage.repositories import users
from fantasy_helper.storage.repositories.runs import record_run

app = typer.Typer(help="Run the scheduled jobs by hand.", no_args_is_help=True)


@app.command("daily")
def daily(
    open_report: Annotated[
        bool, typer.Option("--open", help="Open the report in your browser when it's built.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Do everything, then roll back: nothing is saved or logged."
        ),
    ] = False,
) -> None:
    """Ingest, decide lock or pass for every open starter, log each call, build the report."""
    settings = get_settings()
    url, key = require_db_and_key(settings)
    clock = SystemClock()
    started = clock.now()
    events = events_from_settings(settings, RunKind.DAILY, label="Daily run")
    events.started()

    def once() -> DailyResult:
        return _run_once(url, key, settings.data_dir, events, dry_run=dry_run)

    try:
        result, attempts = run_with_retries(
            once,
            events,
            retryable_parts=lambda r: r.retryable_failures(),
            describe_parts=lambda r, n: (
                f"{r.retryable_failures()} leagues failed in a way a retry can fix; "
                "everything else is saved.",
                [p for p in daily_summary(r, n)[1] if "failed" in p],
            ),
            passthrough=(MissingSnapshotError, typer.Exit),
        )
    except MissingSnapshotError as exc:
        events.failed("No snapshot to load yet: run `fh snapshot` first.")
        raise fail(str(exc)) from exc
    except RunStoppedError as stop:
        cause = stop.__cause__
        record_run_safely(url, RunRecord(
            kind=RunKind.DAILY, status=RunStatus.FAILED, started_at=started, ended_at=clock.now(),
            errors=(f"{type(cause).__name__}: {cause}"[:500],),
        ))  # fmt: skip
        # The phone gets the kind of failure, never the exception's text.
        events.failed(f"Stopped: {outcome(stop.kind, stop.attempts)}. Nothing new was saved.")
        raise typer.Exit(code=1) from cause

    path = _write_report(result, settings.data_dir)
    cards = sum(len(r.cards) for r in result.reports)
    events.step("Report built", f"{cards} cards in {len(result.reports)} reports.")
    _print(result, path)

    detail, problems = daily_summary(result, attempts)
    failed = [lg for lg in result.leagues if lg.failure] + [
        lg for lg in result.ingest.leagues if lg.failure
    ]
    status = RunStatus.SUCCEEDED if not failed and not problems else RunStatus.DEGRADED
    record_run_safely(url, RunRecord(
        kind=RunKind.DAILY, status=status, started_at=started, ended_at=clock.now(),
        errors=tuple(result.ingest.errors + result.ingest.warnings),
    ))  # fmt: skip
    if failed:
        events.failed(detail, problems, urgent=not result.reports)
    elif problems:
        events.warned(problems, detail)
    else:
        events.succeeded(detail)
    if open_report and path is not None:
        webbrowser.open(path.resolve().as_uri())


class _RolledBack(Exception):  # noqa: N818 (control flow for --dry-run, not an error)
    def __init__(self, result: DailyResult) -> None:
        self.result = result


def _run_once(
    url: str, key: bytes, data_dir: Path, events: RunEvents, *, dry_run: bool
) -> DailyResult:
    try:
        with transaction(url) as session, make_client() as client:
            owner = users.get_owner(session)
            if owner is None:
                raise fail("no user yet: run `fh league add` first")
            result = run_daily(
                session, client, store=LocalRawStore(data_dir / "raw"), user=owner, key=key,
                events=events,
            )  # fmt: skip
            if dry_run:
                raise _RolledBack(result)  # leaving the transaction by exception rolls it back
            return result
    except _RolledBack as rolled_back:
        typer.echo("dry run: rolled back, nothing saved or logged")
        return rolled_back.result


def _write_report(result: DailyResult, data_dir: Path) -> Path | None:
    """Until the phone app (M3), the report is a local page under the git-ignored data dir."""
    if not result.reports:
        return None
    for_date = result.reports[0].for_date
    names = {str(lg.league.id): lg.league.name for lg in result.leagues}
    page = render(result.reports, title="Daily report", league_names=names)
    path = data_dir / "reports" / for_date.isoformat() / "daily.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def record_run_safely(url: str, run: RunRecord) -> None:
    try:
        with transaction(url) as session:
            record_run(session, run)
    except Exception as exc:  # the run's outcome still reaches the terminal and the phone
        typer.echo(f"⚠ run log not written ({type(exc).__name__})", err=True)


def _print(result: DailyResult, path: Path | None) -> None:
    for lg in result.leagues:
        if lg.failure is not None:
            typer.echo(f"✗ {lg.league.name!r}: {lg.failure.describe}")
            continue
        typer.echo(
            f"✓ {lg.league.name!r}: {lg.calls} lock-or-pass calls ({lg.new_calls} newly logged)"
        )
        for card in lg.report.cards if lg.report else ():
            typer.echo(f"    · {card.title}")
    typer.echo(f"report: {path}" if path else "no lock-in leagues: no report")
