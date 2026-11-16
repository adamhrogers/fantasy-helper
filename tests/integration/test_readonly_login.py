"""The read-only login (ADR 0032): it reads the data and nothing else. Runs
scripts/readonly-login.sql against the migrated test database, the same file the setup script
runs in prod."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from fantasy_helper.cli.main import app
from fantasy_helper.storage.db import make_engine, normalize_url

pytestmark = pytest.mark.db

SQL = (Path(__file__).parents[2] / "scripts" / "readonly-login.sql").read_text()
PASSWORD = "test-only-password"  # noqa: S105 (the role is dropped again after each test)
KEPT_OUT = {"platform_credentials", "push_subscriptions"}
WRITES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def apply_login(engine: Engine) -> None:
    """Run the setup file as the database's owner, the way psql does with `-v pw=...`."""
    sql = SQL.replace(":'pw'", f"'{PASSWORD}'")
    assert ":'pw'" not in sql
    raw = engine.raw_connection()
    try:
        raw.cursor().execute(sql)  # no parameters: the % in format() must stay literal
        raw.commit()
    finally:
        raw.close()


@pytest.fixture
def login(engine: Engine) -> Iterator[None]:
    apply_login(engine)
    yield
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP OWNED BY fh_readonly")
        conn.exec_driver_sql("DROP ROLE fh_readonly")


def public_tables(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p') order by 1"
        )
    )
    return [name for (name,) in rows]


def can(conn: Connection, table: str, privilege: str) -> bool:
    return bool(
        conn.execute(
            text("select has_table_privilege('fh_readonly', :table, :privilege)"),
            {"table": f'public."{table}"', "privilege": privilege},
        ).scalar_one()
    )


def login_url(database_url: str) -> str:
    url = make_url(normalize_url(database_url)).set(username="fh_readonly", password=PASSWORD)
    return url.render_as_string(hide_password=False)


def login_engine(database_url: str) -> Engine:
    return make_engine(login_url(database_url))


def test_it_reads_every_table_except_the_kept_out_ones_and_writes_none(
    engine: Engine, login: None
) -> None:
    with engine.connect() as conn:
        tables = public_tables(conn)
        assert "platform_credentials" in tables  # the encrypted ESPN logins exist, and are kept out
        for table in tables:
            assert can(conn, table, "SELECT") is (table not in KEPT_OUT), table
            for privilege in WRITES:
                assert not can(conn, table, privilege), (table, privilege)


def test_row_level_security_shows_it_rows_only_in_the_tables_it_may_read(
    engine: Engine, login: None
) -> None:
    with engine.connect() as conn:
        outer = conn.begin()
        conn.execute(text("insert into players (id, full_name) values (gen_random_uuid(), 'X')"))
        assert conn.execute(text("select count(*) from players")).scalar_one() >= 1
        conn.exec_driver_sql("SET LOCAL ROLE fh_readonly")
        assert conn.execute(text("select count(*) from players")).scalar_one() >= 1
        outer.rollback()
    with engine.connect() as conn:
        conn.exec_driver_sql("SET LOCAL ROLE fh_readonly")
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("select count(*) from platform_credentials"))


def test_a_real_login_can_read_but_not_write_even_if_it_switches_the_setting_off(
    engine: Engine, login: None, database_url: str
) -> None:
    mine = login_engine(database_url)
    try:
        with mine.connect() as conn:
            assert conn.execute(text("select 1")).scalar_one() == 1
            assert conn.execute(text("show default_transaction_read_only")).scalar_one() == "on"
            conn.exec_driver_sql("set default_transaction_read_only = off")
            conn.commit()  # the setting applies to the next transaction, not the one running
            assert conn.execute(text("show default_transaction_read_only")).scalar_one() == "off"
            conn.rollback()
            for statement in (
                "insert into players (id, full_name) values (gen_random_uuid(), 'X')",
                "delete from players",
                "truncate players",
                "create table zz_probe (a int)",
                "select * from platform_credentials",
            ):
                with pytest.raises(DBAPIError, match="permission denied"):
                    conn.exec_driver_sql(statement)
                conn.rollback()
    finally:
        mine.dispose()


def test_it_is_a_plain_role_with_a_small_footprint(engine: Engine, login: None) -> None:
    with engine.connect() as conn:
        role = conn.execute(
            text(
                "select rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication, "
                "rolcanlogin, rolconnlimit from pg_roles where rolname = 'fh_readonly'"
            )
        ).one()
        assert tuple(role) == (False, False, False, False, False, True, 3)
        members = conn.execute(
            text(
                "select count(*) from pg_auth_members m join pg_roles r on r.oid = m.member "
                "where r.rolname = 'fh_readonly'"
            )
        ).scalar_one()
        assert members == 0
        assert not conn.execute(
            text("select has_schema_privilege('fh_readonly', 'public', 'CREATE')")
        ).scalar_one()
        settings = dict(
            row.split("=", 1)
            for (row,) in conn.execute(
                text(
                    "select unnest(setconfig) from pg_db_role_setting s "
                    "join pg_roles r on r.oid = s.setrole where r.rolname = 'fh_readonly'"
                )
            )
        )
        assert settings["default_transaction_read_only"] == "on"
        assert settings["statement_timeout"] == "30s"


def test_no_function_runs_with_more_power_than_its_caller(engine: Engine, login: None) -> None:
    """A SELECT that calls a SECURITY DEFINER function could write as the owner. We have none."""
    with engine.connect() as conn:
        definers = conn.execute(
            text(
                "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                "where n.nspname = 'public' and p.prosecdef"
            )
        ).all()
    assert definers == []


def test_a_new_table_stays_hidden_until_the_file_runs_again(engine: Engine, login: None) -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("create table zz_new (a int)")
    try:
        with engine.connect() as conn:
            assert not can(conn, "zz_new", "SELECT")
        apply_login(engine)  # also proves the file can run twice
        with engine.connect() as conn:
            assert can(conn, "zz_new", "SELECT")
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql("drop table zz_new")


runner = CliRunner()


def test_fh_sql_reads_through_the_login(
    engine: Engine, login: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FH_READONLY_DATABASE_URL", login_url(database_url))
    result = runner.invoke(app, ["sql", "select 'abc' like 'a%' as matched, null as nothing"])
    assert result.exit_code == 0, result.output
    assert "matched" in result.stdout
    assert "NULL" in result.stdout
    assert "(1 row)" in result.stdout  # a % in the query is a %, not a placeholder


def test_fh_sql_stops_at_the_limit(
    engine: Engine, login: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FH_READONLY_DATABASE_URL", login_url(database_url))
    result = runner.invoke(app, ["sql", "--limit", "2", "select generate_series(1, 5) as n"])
    assert result.exit_code == 0, result.output
    assert "(2 rows) (more rows exist" in result.stdout


def test_fh_sql_cannot_write_and_says_so(
    engine: Engine, login: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FH_READONLY_DATABASE_URL", login_url(database_url))
    result = runner.invoke(app, ["sql", "delete from players"])
    assert result.exit_code == 1
    assert "denied" in result.output or "read-only" in result.output


def test_fh_sql_refuses_a_login_that_can_write(
    engine: Engine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's URL in the read-only variable by mistake: it runs nothing."""
    monkeypatch.setenv("FH_READONLY_DATABASE_URL", database_url)
    result = runner.invoke(app, ["sql", "select 1"])
    assert result.exit_code == 1
    assert "isn't read-only" in result.output
