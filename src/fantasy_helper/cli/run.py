"""`fh run daily` and `fh run weekly`: the scheduled runs. Cloud Run starts them every morning
(ADR 0026); by hand they work the same way against whichever database `.env` names."""

import time
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

from fantasy_helper.adapters._http import make_client
from fantasy_helper.cli._common import (
    REPORTED_EXIT,
    crash_guard,
    fail,
    open_raw_store,
    pipeline_transaction,
    require_db_and_key,
    transaction,
    watched,
    write_report_page,
)
from fantasy_helper.cli._runs import (
    REQUIRE_SNAPSHOT_TODAY,
    finish_run,
    run_to_completion,
    start_next,
)
from fantasy_helper.config import get_settings
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord
from fantasy_helper.observability.events import RunEvents, events_from_settings
from fantasy_helper.observability.healthchecks import heartbeat_from_settings
from fantasy_helper.pipeline.daily import DailyResult, daily_summary, run_daily
from fantasy_helper.pipeline.freshness import require_snapshot_today
from fantasy_helper.pipeline.weekly import WeeklyResult, run_weekly, weekly_summary
from fantasy_helper.storage.raw_store import RawStore
from fantasy_helper.storage.repositories import users
from fantasy_helper.storage.repositories.runs import record_run

app = typer.Typer(help="Run the scheduled jobs by hand.", no_args_is_help=True)


def _sleep(seconds: float) -> None:
    """Retry waits. A module function so tests can replace it."""
    time.sleep(seconds)


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
    require_today: REQUIRE_SNAPSHOT_TODAY = False,
) -> None:
    """Ingest, then last night's recap for every league and, in lock-in leagues, lock or pass for
    every open starter (each call logged); build the report."""
    settings = get_settings()
    clock = SystemClock()
    started = clock.now()
    events = events_from_settings(settings, RunKind.DAILY, label="Daily run")
    # Everything, including reading the settings, runs inside the watchdog: a bad secret fails the
    # check at once (and the phone hears) instead of an hour later at the end of its grace.
    with (
        watched(heartbeat_from_settings(settings, "daily")),
        crash_guard(settings, RunKind.DAILY, "Daily run"),
    ):
        url, key = require_db_and_key(settings)
        store = open_raw_store(settings)
        events.started()

        def once() -> DailyResult:
            if require_today:
                require_snapshot_today(store, clock.now())
            return _run_once(url, key, store, events, dry_run=dry_run)

        result, attempts = run_to_completion(
            once,
            events,
            kind=RunKind.DAILY,
            started=started,
            now=clock.now,
            retryable_parts=lambda r: r.retryable_failures(),
            describe_parts=lambda r, n: (
                f"{r.retryable_failures()} leagues failed in a way a retry can fix; "
                "everything else is saved.",
                [p for p in daily_summary(r, n)[1] if "failed" in p],
            ),
            after_stop="Nothing new was saved.",
            log_failed=lambda run: record_run_safely(url, run),
            sleep=_sleep,
        )

        names = {str(lg.league.id): lg.league.name for lg in result.leagues}
        path = write_report_page(result.reports, names, settings.data_dir, "daily", "Daily report")
        cards = sum(len(r.cards) for r in result.reports)
        events.step("Report built", f"{cards} cards in {len(result.reports)} reports.")
        _print(result, path)

        detail, problems = daily_summary(result, attempts)
        failed = [lg for lg in result.leagues if lg.failure] + [
            lg for lg in result.ingest.leagues if lg.failure
        ]
        finish_run(
            events,
            kind=RunKind.DAILY,
            started=started,
            ended=clock.now(),
            failed_parts=len(failed),
            urgent=not result.reports,
            detail=detail,
            problems=problems,
            errors=result.ingest.errors + result.ingest.warnings,
            log_run=lambda run: record_run_safely(url, run),
        )
        if open_report and path is not None:
            webbrowser.open(path.resolve().as_uri())
        if not dry_run:
            # The weekly overview is next in the morning chain: it starts when this run is done,
            # whatever its result (it checks its own inputs), never before.
            start_next(settings, events, after="Daily run")
        if failed:
            raise typer.Exit(code=REPORTED_EXIT)  # the events above already told the phone


class _RolledBack(Exception):  # noqa: N818 (control flow for --dry-run, not an error)
    def __init__(self, result: DailyResult) -> None:
        self.result = result


def _run_once(
    url: str, key: bytes, store: RawStore, events: RunEvents, *, dry_run: bool
) -> DailyResult:
    try:
        with pipeline_transaction(url) as session, make_client() as client:
            owner = users.get_owner(session)
            if owner is None:
                raise fail("no user yet: run `fh league add` first")
            result = run_daily(
                session, client, store=store, user=owner, key=key, events=events,
            )  # fmt: skip
            if dry_run:
                raise _RolledBack(result)  # leaving the transaction by exception rolls it back
            return result
    except _RolledBack as rolled_back:
        typer.echo("dry run: rolled back, nothing saved or logged")
        return rolled_back.result


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
        if lg.recap_skipped and lg.report is None:
            typer.echo(
                f"- {lg.league.name!r}: no report (settings, the schedule or your team aren't "
                "loaded yet)"
            )
            continue
        calls = (
            f"{lg.calls} lock-or-pass calls ({lg.new_calls} newly logged), " if lg.lock_in else ""
        )
        typer.echo(f"✓ {lg.league.name!r}: {calls}{lg.recap_cards} recap cards")
        if lg.recap_failure is not None:
            typer.echo(f"  ⚠ recap cards failed: {lg.recap_failure.describe}")
        if lg.recap_skipped:
            typer.echo("  ⚠ recap skipped: settings, the schedule or your team aren't loaded yet")
        for card in lg.report.cards if lg.report else ():
            typer.echo(f"    · {card.title}")
    typer.echo(f"report: {path}" if path else "no report")


@app.command("weekly")
def weekly(
    open_report: Annotated[
        bool, typer.Option("--open", help="Open the overview in your browser when it's built.")
    ] = False,
    require_today: REQUIRE_SNAPSHOT_TODAY = False,
) -> None:
    """Ingest, then the weekly overview for every league: matchup, games, streaming moves."""
    settings = get_settings()
    clock = SystemClock()
    started = clock.now()
    events = events_from_settings(settings, RunKind.WEEKLY, label="Weekly run")
    with (
        watched(heartbeat_from_settings(settings, "weekly")),
        crash_guard(settings, RunKind.WEEKLY, "Weekly run"),
    ):
        url, key = require_db_and_key(settings)
        store = open_raw_store(settings)
        events.started()

        def once() -> WeeklyResult:
            if require_today:
                require_snapshot_today(store, clock.now())
            with pipeline_transaction(url) as session, make_client() as client:
                owner = users.get_owner(session)
                if owner is None:
                    raise fail("no user yet: run `fh league add` first")
                return run_weekly(
                    session, client, store=store, user=owner, key=key, events=events,
                )  # fmt: skip

        result, attempts = run_to_completion(
            once,
            events,
            kind=RunKind.WEEKLY,
            started=started,
            now=clock.now,
            retryable_parts=lambda r: r.retryable_failures(),
            describe_parts=lambda r, n: (
                f"{r.retryable_failures()} leagues failed in a way a retry can fix.",
                [p for p in weekly_summary(r, n)[1] if "failed" in p],
            ),
            after_stop="Nothing new was saved.",
            log_failed=lambda run: record_run_safely(url, run),
            sleep=_sleep,
        )

        names = {str(lg.league.id): lg.league.name for lg in result.leagues}
        path = write_report_page(
            result.reports, names, settings.data_dir, "weekly", "Weekly overview"
        )
        events.step("Report built", f"{sum(len(r.cards) for r in result.reports)} cards in "
                    f"{len(result.reports)} reports.")  # fmt: skip
        for lg in result.leagues:
            if lg.failure is not None:
                typer.echo(f"✗ {lg.league.name!r}: {lg.failure.describe}")
                continue
            typer.echo(f"✓ {lg.league.name!r}: {lg.calls} calls ({lg.new_calls} newly logged)")
            for card in lg.report.cards if lg.report else ():
                typer.echo(f"    · {card.title}")
        typer.echo(f"report: {path}" if path else "no report")

        detail, problems = weekly_summary(result, attempts)
        failed = [lg for lg in result.leagues if lg.failure]
        finish_run(
            events,
            kind=RunKind.WEEKLY,
            started=started,
            ended=clock.now(),
            failed_parts=len(failed),
            urgent=not result.reports,
            detail=detail,
            problems=problems,
            errors=result.ingest.errors + result.ingest.warnings,
            log_run=lambda run: record_run_safely(url, run),
        )
        if open_report and path is not None:
            webbrowser.open(path.resolve().as_uri())
        start_next(settings, events, after="Weekly run")  # the last link: nothing set, no-op
        if failed:
            raise typer.Exit(code=REPORTED_EXIT)  # the events above already told the phone
