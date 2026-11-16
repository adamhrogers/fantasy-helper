"""`fh sql`'s output and its refusal without a login. The database side is tested in
tests/integration/test_readonly_login.py."""

from typer.testing import CliRunner

from fantasy_helper.cli.main import app
from fantasy_helper.cli.sql import MAX_CELL, format_table

runner = CliRunner()


def test_columns_line_up_and_nulls_are_named() -> None:
    text = format_table(["id", "name"], [(1, "Ann"), (22, None)])
    assert text.splitlines() == ["id  name", "--  ----", "1   Ann", "22  NULL"]


def test_long_cells_are_cut_unless_wide() -> None:
    long = "x" * 100
    assert format_table(["a"], [(long,)]).splitlines()[2] == "x" * (MAX_CELL - 1) + "…"
    assert format_table(["a"], [(long,)], wide=True).splitlines()[2] == long


def test_no_rows_still_shows_the_columns() -> None:
    assert format_table(["a", "bb"], []).splitlines() == ["a  bb", "-  --"]


def test_newlines_in_a_cell_dont_break_the_table() -> None:
    assert format_table(["a"], [("one\ntwo",)]).splitlines()[2] == "one two"


def test_without_a_read_only_url_it_says_how_to_get_one() -> None:
    result = runner.invoke(app, ["sql", "select 1"])
    assert result.exit_code == 1
    assert "FH_READONLY_DATABASE_URL" in result.output
    assert "scripts/fh-sql.sh" in result.output
