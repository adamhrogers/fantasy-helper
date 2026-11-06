"""The `fh` command-line interface. Commands are added milestone by milestone."""

import secrets
from typing import Annotated

import typer

from fantasy_helper import __version__
from fantasy_helper.cli.doctor import Check, Status, run_checks
from fantasy_helper.config import get_settings
from fantasy_helper.observability.logs import configure_logging
from fantasy_helper.storage.crypto import generate_key

app = typer.Typer(
    name="fh",
    help="Fantasy Helper operator CLI.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # locals can hold secrets
)

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
