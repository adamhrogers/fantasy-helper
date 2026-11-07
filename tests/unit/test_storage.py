import pytest

from fantasy_helper.storage import migrate
from fantasy_helper.storage.db import normalize_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u@h/db", "postgresql+psycopg://u@h/db"),
    ],
)
def test_normalize_url_uses_psycopg(given: str, expected: str) -> None:
    assert normalize_url(given) == expected


def test_migrations_have_a_single_head() -> None:
    assert migrate.head_revision() == "0004"
