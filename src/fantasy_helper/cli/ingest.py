"""`fh ingest`: load every league and the public NBA data into the database."""

import time
from pathlib import Path

import typer

from fantasy_helper.adapters._http import make_client
from fantasy_helper.cli._common import fail, require_db_and_key, transaction
from fantasy_helper.config import get_settings
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.events import RunEvents, events_from_settings
from fantasy_helper.pipeline.ingest import (
    MB,
    IngestReport,
    MissingSnapshotError,
    phone_summary,
    run_ingest,
)
from fantasy_helper.pipeline.retry import MAX_ATTEMPTS, classify_exception, outcome, wait_before
from fantasy_helper.storage.raw_store import LocalRawStore
from fantasy_helper.storage.repositories import users
from fantasy_helper.storage.repositories.runs import record_run


def _sleep(seconds: float) -> None:
    """Retry waits. A module function so tests can replace it."""
    time.sleep(seconds)


def ingest() -> None:
    """Load all your leagues, the schedule, projections and injuries into the database."""
    settings = get_settings()
    url, key = require_db_and_key(settings)
    clock = SystemClock()
    started = clock.now()
    events = events_from_settings(settings, RunKind.INGEST, label="Ingest")
    events.started()
    report: IngestReport | None = None
    attempt = 1
    # Ingest is idempotent, so a retry is simply another full run: whatever loaded stays loaded,
    # and the leagues (or the whole run) that failed in a retryable way get another go.
    while True:
        try:
            report = _run_once(url, key, settings.data_dir, events)
        except MissingSnapshotError as exc:
            events.failed("No snapshot to load yet: run `fh snapshot` first.")
            raise fail(str(exc)) from exc
        except typer.Exit:
            raise
        except Exception as exc:
            kind = classify_exception(exc)
            if kind.retryable and attempt < MAX_ATTEMPTS:
                attempt += 1
                wait = wait_before(attempt)
                events.retrying(
                    attempt, MAX_ATTEMPTS, wait, f"Stopped: {kind.describe}. Nothing was saved."
                )
                _sleep(wait)
                continue
            _log_run(url, RunRecord(
                kind=RunKind.INGEST, status=RunStatus.FAILED, started_at=started,
                ended_at=clock.now(), errors=(f"{type(exc).__name__}: {exc}"[:500],),
            ))  # fmt: skip
            # The exception's text can hold league data; the phone gets only its kind.
            events.failed(
                f"Stopped: {outcome(kind, attempt)}. Nothing was saved (one transaction)."
            )
            raise
        retry = [lg for lg in report.leagues if lg.failure is not None and lg.failure.retryable]
        if not retry or attempt >= MAX_ATTEMPTS:
            break
        attempt += 1
        wait = wait_before(attempt)
        _, problems = phone_summary(report, attempt - 1)
        events.retrying(
            attempt, MAX_ATTEMPTS, wait,
            f"{len(retry)} of {len(report.leagues)} leagues failed in a way a retry can fix; "
            "everything else is saved.",
            [p for p in problems if "failed" in p],
        )  # fmt: skip
        _sleep(wait)

    _print(report)
    status = (
        RunStatus.SUCCEEDED if not report.errors and not report.warnings else RunStatus.DEGRADED
    )
    rows = (
        report.projection_rows
        + report.injury_changes
        + report.game_log_rows
        + sum(lg.roster_entries + lg.matchups + lg.transactions for lg in report.leagues)
    )
    _log_run(url, RunRecord(
        kind=RunKind.INGEST, status=status, started_at=started, ended_at=clock.now(),
        rows_loaded=rows, errors=tuple(report.errors + report.warnings),
    ))  # fmt: skip
    detail, problems = phone_summary(report, attempt)
    failed = [lg for lg in report.leagues if lg.failure is not None]
    if failed:
        events.failed(detail, problems, urgent=len(failed) == len(report.leagues))
    elif problems:
        events.warned(problems, detail)
    else:
        events.succeeded(detail)
    typer.echo(f"ingest {status.value} after {attempt} attempt(s), run logged")
    if report.errors:
        raise typer.Exit(code=1)


def _run_once(url: str, key: bytes, data_dir: Path, events: RunEvents) -> IngestReport:
    with transaction(url) as session, make_client() as client:
        owner = users.get_owner(session)
        if owner is None:
            raise fail("no user yet: run `fh league add` first")
        return run_ingest(
            session, client, store=LocalRawStore(data_dir / "raw"), user=owner, key=key,
            events=events,
        )  # fmt: skip


def _log_run(url: str, run: RunRecord) -> None:
    with transaction(url) as session:
        record_run(session, run)


def _print(report: IngestReport) -> None:
    cw = report.crosswalk
    linked = ", ".join(
        f"{n} by {method.replace('_', ' + ')}" for method, n in sorted(cw.linked.items())
    )
    typer.echo(
        f"✓ players: {cw.created} new from ESPN; Sleeper links: {linked or 'none new'}; "
        f"{len(cw.unmatched)} Sleeper players unmatched; "
        f"{cw.birth_date_conflicts} linked pairs with conflicting birth dates (data errors)"
    )
    typer.echo(f"✓ schedule: {report.games} games")
    typer.echo(
        f"✓ projections: {report.projection_snapshots} snapshots, {report.projection_rows} rows"
    )
    typer.echo(f"✓ injury status changes: {report.injury_changes}")
    skipped = report.game_logs_unresolved + report.game_logs_unknown_game
    typer.echo(
        f"✓ box scores: {report.game_log_rows} rows from {report.game_log_payloads} new payloads"
        + (f" ({skipped} skipped)" if skipped else "")
    )
    for lg in report.leagues:
        if lg.failure is not None:
            typer.echo(f"✗ {lg.league.platform.value:<8} {lg.league.name!r}: {lg.failure.describe}")
            continue
        settings = f"settings v{lg.settings_version}{' (new)' if lg.new_settings else ''}"
        extra = []
        if lg.trades_without_players:
            extra.append(
                f"{lg.trades_without_players} other-team trades without players (ESPN hides them)"
            )
        if lg.rostered_sleeper:
            extra.append(
                f"{lg.rostered_sleeper_linked}/{lg.rostered_sleeper} rostered linked to ESPN"
            )
            extra.append(f"{lg.free_agents} free agents ({lg.free_agents_linked} linked to ESPN)")
        counts = (
            f"{lg.teams} teams · {lg.roster_entries} rostered · {lg.matchups} matchups · "
            f"{lg.transactions} transactions"
        )
        tail = f" · {'; '.join(extra)}" if extra else ""
        typer.echo(
            f"✓ {lg.league.platform.value:<8} {lg.league.name!r}: {settings} · {counts}{tail}"
        )
    typer.echo(f"✓ database size: {report.database_bytes / MB:.0f} MB of 500 MB")
    for warning in report.warnings:
        typer.echo(f"⚠ {warning}")
    for error in report.errors:
        typer.echo(f"✗ {error}")
