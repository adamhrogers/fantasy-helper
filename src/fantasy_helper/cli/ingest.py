"""`fh ingest`: load every league and the public NBA data into the database."""

import typer

from fantasy_helper.adapters._http import make_client
from fantasy_helper.cli._common import fail, require_db_and_key, transaction
from fantasy_helper.config import get_settings
from fantasy_helper.domain.ports import Alert, AlertPriority, SystemClock
from fantasy_helper.domain.runs import RunKind, RunRecord, RunStatus
from fantasy_helper.observability.alerts import notifier_from_settings
from fantasy_helper.pipeline.ingest import IngestReport, MissingSnapshotError, run_ingest
from fantasy_helper.storage.raw_store import LocalRawStore
from fantasy_helper.storage.repositories import users
from fantasy_helper.storage.repositories.runs import record_run


def ingest() -> None:
    """Load all your leagues, the schedule, projections and injuries into the database."""
    settings = get_settings()
    url, key = require_db_and_key(settings)
    clock = SystemClock()
    started = clock.now()
    notifier = notifier_from_settings(settings)
    try:
        with transaction(url) as session, make_client() as client:
            owner = users.get_owner(session)
            if owner is None:
                raise fail("no user yet: run `fh league add` first")
            report = run_ingest(
                session, client, store=LocalRawStore(settings.data_dir / "raw"), user=owner, key=key
            )
    except MissingSnapshotError as exc:
        raise fail(str(exc)) from exc
    except typer.Exit:
        raise
    except Exception as exc:
        _log_run(url, RunRecord(
            kind=RunKind.INGEST, status=RunStatus.FAILED, started_at=started,
            ended_at=clock.now(), errors=(f"{type(exc).__name__}: {exc}"[:500],),
        ))  # fmt: skip
        notifier.send(Alert(title="Ingest failed", message=type(exc).__name__,
                            priority=AlertPriority.HIGH, tags=("warning",)))  # fmt: skip
        raise

    _print(report)
    status = (
        RunStatus.SUCCEEDED if not report.errors and not report.warnings else RunStatus.DEGRADED
    )
    rows = (
        report.projection_rows
        + report.injury_changes
        + sum(lg.roster_entries + lg.matchups + lg.transactions for lg in report.leagues)
    )
    _log_run(url, RunRecord(
        kind=RunKind.INGEST, status=status, started_at=started, ended_at=clock.now(),
        rows_loaded=rows, errors=tuple(report.errors + report.warnings),
    ))  # fmt: skip
    if report.errors or report.warnings:
        notifier.send(Alert(
            title="Ingest needs attention",
            message="\n".join(report.errors + report.warnings)[:2000],
            priority=AlertPriority.HIGH if report.errors else AlertPriority.DEFAULT,
            tags=("warning",),
        ))  # fmt: skip
    typer.echo(f"ingest {status.value}, run logged")
    if report.errors:
        raise typer.Exit(code=1)


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
        f"{len(cw.unmatched)} Sleeper players unmatched"
    )
    typer.echo(f"✓ schedule: {report.games} games")
    typer.echo(
        f"✓ projections: {report.projection_snapshots} snapshots, {report.projection_rows} rows"
    )
    typer.echo(f"✓ injury status changes: {report.injury_changes}")
    for lg in report.leagues:
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
        counts = (
            f"{lg.teams} teams · {lg.roster_entries} rostered · {lg.matchups} matchups · "
            f"{lg.transactions} transactions"
        )
        tail = f" · {'; '.join(extra)}" if extra else ""
        typer.echo(
            f"✓ {lg.league.platform.value:<8} {lg.league.name!r}: {settings} · {counts}{tail}"
        )
    for warning in report.warnings:
        typer.echo(f"⚠ {warning}")
    for error in report.errors:
        typer.echo(f"✗ {error}")
