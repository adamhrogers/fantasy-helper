"""`fh report open`: read a saved report back and show it as a local page, until the phone app
(M3 parts 3 and 4) shows them. Works against whichever database DATABASE_URL names, so
`scripts/fh-prod.sh report open` shows what the scheduled jobs built in the cloud (ADR 0026)."""

import webbrowser
from datetime import date
from enum import StrEnum
from typing import Annotated

import typer

from fantasy_helper.cli._common import fail, transaction, write_report_page
from fantasy_helper.config import get_settings
from fantasy_helper.domain.reports import ReportKind
from fantasy_helper.storage.repositories import leagues, reports, users

app = typer.Typer(help="Show reports saved in the database.", no_args_is_help=True)


class OpenKind(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


@app.command("open")
def open_saved(
    kind: Annotated[OpenKind, typer.Argument(help="daily or weekly")] = OpenKind.DAILY,
    for_date: Annotated[
        str | None, typer.Option("--date", help="YYYY-MM-DD. Default: the latest saved.")
    ] = None,
    print_only: Annotated[
        bool, typer.Option("--print-only", help="Write the page and print its path; no browser.")
    ] = False,
) -> None:
    """Open the latest saved daily or weekly report in your browser."""
    settings = get_settings()
    if settings.database_url is None:
        raise fail("DATABASE_URL is not set (see .env.example)")
    try:
        day = date.fromisoformat(for_date) if for_date else None
    except ValueError as exc:
        raise fail(f"--date must look like 2026-11-15, not {for_date!r}") from exc
    with transaction(settings.database_url.get_secret_value()) as session:
        owner = users.get_owner(session)
        if owner is None:
            raise fail("no user yet: run `fh league add` first")
        found = reports.for_user(session, owner.id, ReportKind(kind.value), day)
        names = {str(lg.id): lg.name for lg in leagues.list_for_user(session, owner.id)}
    if not found:
        when = f"for {day}" if day else "yet"
        raise fail(f"no saved {kind.value} reports {when}. The scheduled run builds them.")
    # The local file name says it came from the database, so it never overwrites a page
    # `fh run daily --open` built on this laptop.
    path = write_report_page(
        found,
        names,
        settings.data_dir,
        f"{kind.value}-saved",
        f"{kind.value.title()} report",
        for_date=found[0].for_date,
    )
    assert path is not None  # noqa: S101 (found is not empty)
    cards = sum(len(r.cards) for r in found)
    typer.echo(f"{len(found)} reports, {cards} cards, for {found[0].for_date}: {path}")
    if not print_only:
        webbrowser.open(path.resolve().as_uri())
