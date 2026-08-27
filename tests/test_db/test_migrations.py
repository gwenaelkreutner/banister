"""Schema lifecycle tests (spec 003 FR-018..021).

Every test here drives the real Alembic command API against a scratch PostgreSQL
database — this is deliberately not simulated, because the property under test
(what Alembic actually does with this project's revision history) is exactly what a
mock would remove.

Skipped entirely when PostgreSQL is unreachable, with a stated reason (same policy as
tests/test_db/conftest.py). SQLite is not exercised here: Alembic's own behaviour is
backend-agnostic, and standing up a throwaway SQLite file to prove the same thing this
suite already proves against a real database would test Alembic, not this project.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.exceptions import SchemaTooNewError

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://banister:password@localhost:5432/banister_test",
)
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "migrations")


def _config(url: str) -> Config:
    cfg = Config(os.path.join(MIGRATIONS_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _upgrade(cfg: Config, revision: str = "head") -> None:
    """Alembic's command API is synchronous and drives its own asyncio.run() inside
    migrations/env.py — calling it directly from an async test body hits 'asyncio.run()
    cannot be called from a running event loop'. Dispatching to a thread is the same
    pattern app/db/lifecycle.run_migrations() uses in production, for the same reason."""
    await asyncio.to_thread(command.upgrade, cfg, revision)


async def _postgres_reachable() -> bool:
    try:
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
async def scratch_db_url():
    """A dedicated, empty database per test — migrations tests need full control over
    schema state (blank, mid-history, corrupted) that the shared db_session fixture's
    create-all/drop-all cycle doesn't give us."""
    if not await _postgres_reachable():
        pytest.skip(
            f"PostgreSQL not reachable at {TEST_DATABASE_URL} — schema lifecycle tests "
            "need a real database; Alembic's behaviour is not meaningfully mockable."
        )

    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = "banister_migrations_scratch"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{db_name}"
    yield url

    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    await admin_engine.dispose()


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
    await engine.dispose()
    return set(names)


async def test_upgrade_from_blank_creates_the_full_schema(scratch_db_url):
    await _upgrade(_config(scratch_db_url))
    tables = await _table_names(scratch_db_url)
    assert {
        "users", "athlete_profiles", "training_plans", "session_logs",
        "activities", "chat_messages", "weekly_adherence", "oauth_connections",
        "alembic_version",
    } <= tables


async def test_upgrade_is_idempotent_when_already_current(scratch_db_url):
    """FR-019: running against an already-current schema makes no changes."""
    cfg = _config(scratch_db_url)
    await _upgrade(cfg)
    before = await _table_names(scratch_db_url)

    await _upgrade(cfg)  # second call, same target
    after = await _table_names(scratch_db_url)

    assert before == after


async def test_refuses_to_start_against_an_unknown_newer_revision(scratch_db_url, monkeypatch):
    """FR-021. Calls the shipped _upgrade_to_head_sync directly rather than
    reimplementing its wrapping logic — this proves the production code path raises
    SchemaTooNewError, not merely that Alembic can be made to fail somehow.

    settings.database_url is patched directly rather than via the DATABASE_URL
    environment variable, because app.config.settings is a module-level singleton
    already constructed by import time; mutating the environment afterwards has no
    effect on it."""
    engine = create_async_engine(scratch_db_url)
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        await conn.execute(text(
            "INSERT INTO alembic_version VALUES ('a_revision_that_does_not_exist')"
        ))
    await engine.dispose()

    from app.db import lifecycle

    monkeypatch.setattr(lifecycle.settings, "database_url", scratch_db_url)

    with pytest.raises(SchemaTooNewError):
        await asyncio.to_thread(lifecycle._upgrade_to_head_sync)


async def test_a_failed_migration_leaves_the_previous_schema_intact(scratch_db_url):
    """FR-020. PostgreSQL wraps each migration in a transaction ('Will assume
    transactional DDL', observed in this project's own alembic output) — this test
    pins that guarantee rather than trusting the log line."""
    cfg = _config(scratch_db_url)
    await _upgrade(cfg)
    tables_before = await _table_names(scratch_db_url)

    # Inject a migration that fails partway through, targeting a table Alembic already
    # knows exists so this is unambiguously a mid-migration failure, not a setup error.
    broken_dir = os.path.join(MIGRATIONS_DIR, "versions")
    broken_path = os.path.join(broken_dir, "zzzz_broken_test_revision.py")

    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    broken_migration = f'''"""broken test revision"""
revision = "zzzz_broken_test"
down_revision = "{head_rev}"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column("users", sa.Column("this_column_is_fine", sa.String(10)))
    raise RuntimeError("simulated failure mid-migration")

def downgrade():
    pass
'''
    with open(broken_path, "w", encoding="utf-8") as f:
        f.write(broken_migration)

    try:
        with pytest.raises(Exception):
            await _upgrade(cfg)

        tables_after = await _table_names(scratch_db_url)
        assert tables_after == tables_before

        engine = create_async_engine(scratch_db_url)
        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda c: [col["name"] for col in sa.inspect(c).get_columns("users")]
            )
        await engine.dispose()
        assert "this_column_is_fine" not in columns, (
            "the failed migration's DDL was not rolled back — the transactional-DDL "
            "guarantee this test exists to pin does not hold"
        )
    finally:
        os.remove(broken_path)
        pycache = os.path.join(broken_dir, "__pycache__")
        if os.path.isdir(pycache):
            for f in os.listdir(pycache):
                if "zzzz_broken" in f:
                    os.remove(os.path.join(pycache, f))
