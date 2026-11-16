"""`fh sql`: look at the database with a read-only login (ADR 0032).

Setup and use: docs/runbooks/read-only-access.md.

It reads FH_READONLY_DATABASE_URL and never DATABASE_URL. It runs one query through the database
driver, so psql's backslash commands (which can run shell commands) don't exist here, and it asks
the database to treat the whole session as read-only. Before it runs anything it checks the login
it was given: one that is a superuser, bypasses row-level security or can write to any table is
refused, so pointing it at the owner's URL by mistake does nothing.
"""

from typing import Annotated, Any

import typer

from fantasy_helper.cli._common import fail
from fantasy_helper.config import get_settings
from fantasy_helper.storage.db import make_engine

MAX_CELL = 60  # characters shown per cell unless --wide

_POWER_CHECK = """
select
  (select rolsuper or rolbypassrls from pg_roles where rolname = current_user),
  (select string_agg(c.relname, ', ' order by c.relname)
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and has_table_privilege(current_user, c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE'))
"""


def format_table(columns: list[str], rows: list[tuple[Any, ...]], *, wide: bool = False) -> str:
    """Rows as aligned text. Long cells are cut at MAX_CELL unless `wide`."""

    def cell(value: Any) -> str:
        text = "NULL" if value is None else str(value).replace("\n", " ")
        return text if wide or len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"

    body = [[cell(v) for v in row] for row in rows]
    widths = [max([len(c), *(len(r[i]) for r in body)]) for i, c in enumerate(columns)]
    lines = [
        "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)).rstrip(),
        "  ".join("-" * w for w in widths),
        *("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)).rstrip() for r in body),
    ]
    return "\n".join(lines)


def sql(
    query: Annotated[str, typer.Argument(help="One SQL query. Quote it.")],
    limit: Annotated[int, typer.Option(help="Most rows to show.")] = 200,
    wide: Annotated[bool, typer.Option(help="Don't cut long cells.")] = False,
) -> None:
    """Run one read-only query. Needs FH_READONLY_DATABASE_URL (scripts/fh-sql.sh sets it)."""
    settings = get_settings()
    if settings.readonly_database_url is None:
        raise fail(
            "FH_READONLY_DATABASE_URL is not set. Use scripts/fh-sql.sh for prod "
            "(docs/runbooks/read-only-access.md)."
        )
    if limit < 1:
        raise fail("--limit must be at least 1")
    engine = make_engine(settings.readonly_database_url.get_secret_value())
    conn = engine.raw_connection()
    try:
        raw = conn.driver_connection
        raw.read_only = True  # type: ignore[union-attr]  # every transaction starts READ ONLY
        with raw.cursor() as cur:  # type: ignore[union-attr]
            cur.execute(_POWER_CHECK)
            powerful, writable = cur.fetchone() or (True, None)
            if powerful or writable:
                why = (
                    "it's a superuser or bypasses row-level security"
                    if powerful
                    else f"it can write to: {writable}"
                )
                raise fail(
                    f"refusing to run: this login isn't read-only ({why}). "
                    "FH_READONLY_DATABASE_URL must be the fh_readonly login "
                    "(docs/runbooks/read-only-access.md)."
                )
            try:
                cur.execute(query)  # no parameters: a % in a LIKE stays a %
            except Exception as exc:  # the driver's message is the useful part
                raise fail(str(exc).strip()) from exc
            if cur.description is None:
                typer.echo("(no rows to show: that statement doesn't return any)")
                return
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(limit + 1)
        shown = rows[:limit]
        typer.echo(format_table(columns, shown, wide=wide))
        extra = " (more rows exist: raise --limit)" if len(rows) > limit else ""
        typer.echo(f"({len(shown)} row{'s' if len(shown) != 1 else ''}){extra}")
    finally:
        conn.rollback()
        conn.close()
        engine.dispose()
