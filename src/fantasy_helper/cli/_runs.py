"""What `fh ingest`, `fh run daily` and `fh run weekly` share: retries, one clear message for each
way a run stops, and the exit code the watchdog reads (ADR 0025, 0026)."""

import time
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

import typer

from fantasy_helper.cli._common import REPORTED_EXIT
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.events import RunEvents
from fantasy_helper.pipeline.ingest import MissingSnapshotError
from fantasy_helper.pipeline.retry import RunStoppedError, outcome, run_with_retries

REQUIRE_SNAPSHOT_TODAY = Annotated[
    bool,
    typer.Option(
        "--require-snapshot-today",
        help="Wait for this morning's snapshot (retrying), and fail if it never arrives. "
        "The scheduled jobs set this, so a run never builds from yesterday's box scores.",
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
