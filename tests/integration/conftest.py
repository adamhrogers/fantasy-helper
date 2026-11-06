import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, pool
from sqlalchemy.engine import make_url

from fantasy_helper.storage import migrate
from fantasy_helper.storage.db import make_engine, normalize_url


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("FH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("FH_TEST_DATABASE_URL is not set")
    name = make_url(normalize_url(url)).database or ""
    if "test" not in name:
        pytest.fail(
            "FH_TEST_DATABASE_URL must name a database containing 'test': these tests drop tables"
        )
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    migrate.downgrade(database_url, "base")
    migrate.upgrade(database_url, "head")
    engine = make_engine(database_url, poolclass=pool.NullPool)
    yield engine
    engine.dispose()
    migrate.downgrade(database_url, "base")
