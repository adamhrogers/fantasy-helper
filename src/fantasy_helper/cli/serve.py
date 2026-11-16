"""`fh serve` runs the phone app's API; `fh openapi` prints its schema (ADR 0029)."""

import json
from typing import Annotated

import typer

from fantasy_helper.api.app import create_app
from fantasy_helper.config import get_settings


def serve(
    host: Annotated[str, typer.Option(help="Cloud Run needs every interface.")] = "0.0.0.0",  # noqa: S104 (a container's only interface; Cloud Run fronts it)
    port: Annotated[int, typer.Option(envvar="PORT", help="Cloud Run sets PORT.")] = 8080,
) -> None:
    """Run the API (uvicorn). Needs DATABASE_URL and FH_SUPABASE_URL."""
    import uvicorn  # not needed by the jobs, so it isn't imported for them

    settings = get_settings()
    if settings.supabase_url is None:
        typer.echo("✗ FH_SUPABASE_URL is not set: nobody could sign in", err=True)
        raise typer.Exit(code=1)
    # log_config=None: our own JSON logging (observability/logs.py) is already set up, and
    # uvicorn's access log would carry every URL.
    uvicorn.run(create_app(settings), host=host, port=port, log_config=None, access_log=False)


def openapi() -> None:
    """Print the API's schema as JSON. `web/openapi.json` holds it, and a test checks they match."""
    typer.echo(json.dumps(create_app(get_settings()).openapi(), indent=2, sort_keys=True))
