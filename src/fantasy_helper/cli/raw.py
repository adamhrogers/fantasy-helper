"""`fh raw`: the raw snapshot store (ADR 0023)."""

from pathlib import Path
from typing import Annotated

import typer

from fantasy_helper.cli._common import fail
from fantasy_helper.config import get_settings
from fantasy_helper.pipeline.raw_upload import UploadReport, upload_backlog
from fantasy_helper.storage.r2_store import r2_store_from_settings
from fantasy_helper.storage.raw_store import RawStoreError

app = typer.Typer(help="The raw snapshot store.", no_args_is_help=True)

MAX_LISTED = 10


@app.command("upload")
def upload(
    source: Annotated[
        Path | None,
        typer.Option("--from", help="The local raw folder. Default: FH_DATA_DIR/raw."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Compare only: count what would go up.")
    ] = False,
) -> None:
    """Copy local raw snapshots to R2. Safe to re-run: nothing in R2 is ever overwritten."""
    settings = get_settings()
    root = source or settings.data_dir / "raw"
    if not root.is_dir():
        raise fail(f"no raw folder at {root}")
    try:
        store = r2_store_from_settings(settings)
        report = upload_backlog(root, store, dry_run=dry_run)
    except RawStoreError as exc:
        raise fail(str(exc)) from exc
    _print(report, root)
    if not report.ok:
        raise typer.Exit(code=1)


def _print(report: UploadReport, root: Path) -> None:
    typer.echo(f"{report.local_files} files in {root}")
    typer.echo(f"✓ {report.already_there} already in R2 with the same bytes")
    if report.dry_run:
        typer.echo(f"· {report.to_upload} would be uploaded (dry run: nothing written)")
    else:
        typer.echo(f"✓ {report.uploaded} uploaded and verified")
    for label, names in (
        ("in R2 with different bytes (left alone)", report.conflicts),
        ("payloads without a sidecar (skipped)", report.orphans),
        ("failed", report.failures),
        ("uploaded but not matching R2's listing", report.unverified),
    ):
        if names:
            typer.echo(f"✗ {len(names)} {label}:")
            for name in names[:MAX_LISTED]:
                typer.echo(f"    {name}")
            if len(names) > MAX_LISTED:
                typer.echo(f"    …and {len(names) - MAX_LISTED} more")
