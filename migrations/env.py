import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# migrations/ sits next to app/, not under it — make the package importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.db.models import Base  # noqa: E402  (imports every model so metadata is complete)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config


def _resolve_database_url() -> str:
    """Read DATABASE_URL the same way the application's own env_file does, without
    depending on app.config.Settings — that model requires unrelated fields (bot token,
    etc.) that a migration run has no business needing. Both this function and the app
    read the same variable, so there is still exactly one source of truth for the URL."""
    if "DATABASE_URL" in os.environ:
        return os.environ["DATABASE_URL"]

    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise RuntimeError(
        "DATABASE_URL is not set and no migrations/../.env defines it — migrations need "
        "the same connection string the application uses."
    )


# A caller driving Alembic programmatically (app/db/lifecycle.py, or a test) may already
# have set sqlalchemy.url on the Config object it passed to command.upgrade(). That value
# must win — unconditionally overwriting it here was a real bug, caught by this project's
# own migration tests: connecting to the .env-configured production database instead of
# the test's scratch one, discovered only once the tests actually ran end to end rather
# than being trusted from a code read.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", _resolve_database_url())

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
