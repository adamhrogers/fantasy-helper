"""Alembic environment. The database URL comes from DATABASE_URL, never from a config file."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from fantasy_helper.config import get_settings
from fantasy_helper.storage.db import make_engine, normalize_url
from fantasy_helper.storage.tables import Base

config = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")  # set by storage.migrate for tests and tools
    if url:
        return url
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not set; see .env.example")
    return settings.database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=normalize_url(_database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
