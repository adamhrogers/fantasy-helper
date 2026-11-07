"""`fh fixtures record`: save redacted real responses as test fixtures (ADR 0012)."""

from pathlib import Path
from typing import Annotated

import typer

from fantasy_helper.adapters._http import make_client
from fantasy_helper.adapters.espn.league import EspnAccessError
from fantasy_helper.adapters.redaction import RedactionLeakError
from fantasy_helper.cli._common import fail, require_db_and_key, transaction
from fantasy_helper.config import get_settings
from fantasy_helper.domain.ports import SystemClock
from fantasy_helper.pipeline.fixtures import FIXTURES_ROOT, record_fixtures, write_fixtures
from fantasy_helper.pipeline.onboarding import MissingCredentialsError
from fantasy_helper.storage.repositories import users

app = typer.Typer(
    help="Record redacted test fixtures from your real leagues.", no_args_is_help=True
)


@app.command("record")
def record(
    root: Annotated[Path, typer.Option(help="Where fixtures are written")] = FIXTURES_ROOT,
    only: Annotated[
        list[str] | None,
        typer.Option(
            "--only",
            help="Write only these fixtures (e.g. espn_public/game_logs.json); keep the rest.",
        ),
    ] = None,
) -> None:
    """Fetch every league dataset once, redact it, verify it's clean, then write it."""
    url, key = require_db_and_key(get_settings())
    try:
        with transaction(url) as session, make_client() as client:
            owner = users.get_owner(session)
            if owner is None:
                raise fail("no user yet: run `fh league add` first")
            fixtures = record_fixtures(session, client, user=owner, key=key)
    except RedactionLeakError as exc:
        raise fail(f"nothing written: redaction incomplete ({exc})") from exc
    except (EspnAccessError, MissingCredentialsError) as exc:
        raise fail(str(exc)) from exc
    chosen = frozenset(only) if only else None
    unknown = (chosen or frozenset()) - {f.path for f in fixtures}
    if unknown:
        raise fail(f"no such fixture: {', '.join(sorted(unknown))}")
    written = write_fixtures(fixtures, root, SystemClock().now(), only=chosen)
    for path in written:
        typer.echo(f"✓ {path}  {path.stat().st_size / 1024:.0f} KB")
    typer.echo(f"{len(written)} fixtures written to {root} (redacted and verified)")
