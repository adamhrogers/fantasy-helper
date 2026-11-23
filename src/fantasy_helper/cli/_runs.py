"""What `fh ingest`, `fh run daily` and `fh run weekly` share: retries, one clear message for each
way a run stops, and the exit code the watchdog reads (ADR 0025, 0026)."""

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated

import typer

from fantasy_helper.adapters import cloud_run
from fantasy_helper.cli._common import REPORTED_EXIT, transaction
from fantasy_helper.config import Settings
from fantasy_helper.delivery.push import sender_from_settings
from fantasy_helper.domain.reports import ReportDocument, ReportKind
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.pipeline.ingest import MissingSnapshotError
from fantasy_helper.pipeline.notify import notify_report_ready
from fantasy_helper.pipeline.retry import RunStoppedError, outcome, run_with_retries

REQUIRE_SNAPSHOT_TODAY = Annotated[
    bool,
    typer.Option(
        "--require-snapshot-today",
        help="Wait for today's complete morning snapshot (retrying), and fail if it never "
        "comes. The scheduled jobs set this, so a run never builds from a partial or old one.",
    ),
]


def run_to_completion[T](
    once: Callable[[], T],
    events: RunEvents,
    *,
    kind: RunKind,
    started: datetime,
    now: Callable[[], datetime],
    retryable_parts: Callable[[T], int],
    describe_parts: Callable[[T, int], tuple[str, list[str]]],
    after_stop: str,
    log_failed: Callable[[RunRecord], None],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Run `once` with retries. When the run can't finish, log it as failed, tell the phone what
    kind of failure it was (never the exception's text, which can hold league data), and exit
    REPORTED_EXIT: the phone already knows."""
    try:
        return run_with_retries(
            once,
            events,
            retryable_parts=retryable_parts,
            describe_parts=describe_parts,
            passthrough=(MissingSnapshotError, typer.Exit),
            sleep=sleep,
        )
    except MissingSnapshotError as exc:
        events.failed("No snapshot to load yet: run `fh snapshot` first.")
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(code=REPORTED_EXIT) from exc
    except RunStoppedError as stop:
        cause = stop.__cause__
        log_failed(RunRecord(
            kind=kind, status=RunStatus.FAILED, started_at=started, ended_at=now(),
            errors=(f"{type(cause).__name__}: {cause}"[:500],),
        ))  # fmt: skip
        events.failed(f"Stopped: {outcome(stop.kind, stop.attempts)}. {after_stop}")
        typer.echo(f"✗ stopped: {type(cause).__name__}: {cause}", err=True)
        raise typer.Exit(code=REPORTED_EXIT) from cause


def finish_run(
    events: RunEvents,
    *,
    kind: RunKind,
    started: datetime,
    ended: datetime,
    failed_parts: int,
    urgent: bool,
    detail: str,
    problems: list[str],
    errors: Sequence[str],
    log_run: Callable[[RunRecord], None],
) -> RunStatus:
    """The end of a daily or weekly run: log it and tell the phone, once.

    **Only a part that failed (a league) is a failure.** Warnings, such as a Sleeper player not
    yet linked, a matchup that spans two weeks, or a database nearing its quota, are sent to the
    phone as a warning and recorded as DEGRADED, but they don't make the run exit non-zero: a
    condition that lasts for weeks would otherwise raise a "down" alert every morning and bury the
    failures that matter (ADR 0026)."""
    status = RunStatus.SUCCEEDED if not failed_parts and not problems else RunStatus.DEGRADED
    log_run(RunRecord(
        kind=kind, status=status, started_at=started, ended_at=ended, errors=tuple(errors),
    ))  # fmt: skip
    if failed_parts:
        events.failed(detail, problems, urgent=urgent)
    elif problems:
        events.warned(problems, detail)
    else:
        events.succeeded(detail)
    return status


def start_next(
    settings: Settings,
    events: RunEvents,
    *,
    after: str,
    starter: Callable[[str], None] = cloud_run.start_job,
) -> None:
    """Hand the morning on to the next job in the chain (ADR 0028), when this run has one:
    the morning snapshot starts the daily run, which starts the weekly one. A run started by hand
    has no next job and does nothing here. If the next job can't be started, the phone hears
    about it at once: the data is saved, but the reports won't build until someone starts them."""
    if settings.next_job is None:
        return
    name = settings.next_job.rsplit("/", 1)[-1]
    try:
        starter(settings.next_job)
    except cloud_run.CloudRunError as exc:
        events.failed(
            f"{after} finished, but the next job in the morning chain ({name}) couldn't be "
            f"started: {exc.kind}. Start it by hand (docs/runbooks/prod-database.md).",
            title=f"{after}: couldn't start {name}",
            urgent=True,
        )
        typer.echo(f"✗ couldn't start {name}: {exc.kind}", err=True)
        raise typer.Exit(code=REPORTED_EXIT) from exc
    events.step("Next job started", f"{name} was started.")


def push_report_ready(
    settings: Settings,
    events: RunEvents,
    *,
    url: str,
    kind: ReportKind,
    reports: Sequence[ReportDocument],
    now: datetime,
) -> list[str]:
    """Tell the phone the reports are ready (ADR 0030), once the run has committed them. Returns
    the warnings for the run's ending: the push never fails a run, and the report is built either
    way. Not called on a dry run, which saved nothing."""
    if not reports:
        return []
    notified = notify_report_ready(
        lambda: transaction(url),
        lambda: sender_from_settings(settings),
        user_id=reports[0].user_id,
        kind=kind,
        dates={report.for_date for report in reports},
        now=now,
    )
    if notified.delivered:  # a quiet step; problems reach the phone through the run's ending
        events.step("Push sent", notified.detail())
    typer.echo(f"push: {notified.detail()}")
    return list(notified.problems)
