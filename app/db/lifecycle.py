"""Startup schema lifecycle: automatic migration, guarded in both directions (spec 003).

FR-018: schema changes apply automatically, no manual step.
FR-019: idempotent — starting against an already-current schema makes no changes.
FR-020: a failed migration leaves the database in its previous working state.
FR-021: refuse to start against a schema stamped with a revision this code doesn't know.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

from app.config import settings
from app.core.exceptions import MigrationFailedError, SchemaTooNewError

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def _alembic_config() -> Config:
    cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # The application's settings are the single source of truth for the URL — matches
    # migrations/env.py's own resolution so both agree on what "the database" means.
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def _upgrade_to_head_sync() -> None:
    """Runs Alembic's command API, which is synchronous and internally drives its own
    asyncio.run() inside migrations/env.py — that cannot be nested inside the event loop
    FastAPI's lifespan already runs in, which is why run_migrations() below dispatches
    this to a thread rather than calling it directly."""
    cfg = _alembic_config()
    try:
        command.upgrade(cfg, "head")
    except CommandError as exc:
        message = str(exc)
        if "Can't locate revision" in message:
            # Verified empirically (spec 003 T026): this is the exact message Alembic
            # raises when the database's stamped revision isn't among the revisions this
            # codebase's migrations/versions/ knows about — i.e. the schema is newer than
            # this running version understands. PostgreSQL and SQLite both wrap the
            # migration itself in a transaction ("Will assume transactional DDL"), so a
            # failure here has not partially applied anything.
            raise SchemaTooNewError(
                "The database schema is stamped with a revision this version of the "
                "application does not recognize. Refusing to start rather than writing "
                f"data shaped for a schema it does not understand. ({message})"
            ) from exc
        raise MigrationFailedError(
            f"Schema migration failed; the database was left in its previous working "
            f"state (transactional DDL). Original error: {message}"
        ) from exc


async def run_migrations() -> None:
    """Bring the schema up to date. Call once at startup, before anything else touches
    the database."""
    logger.info("Checking database schema...")
    await asyncio.to_thread(_upgrade_to_head_sync)
    logger.info("Database schema is up to date.")
