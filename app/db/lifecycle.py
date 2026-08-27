"""Startup lifecycle: data directory validation, automatic migration, single-instance
guard (spec 003), training data source credential verification (spec 002).

FR-004: fail at startup with a specific message when the data directory is unusable.
FR-005: refuse a second instance against the same store rather than corrupting it.
FR-018: schema changes apply automatically, no manual step.
FR-019: idempotent — starting against an already-current schema makes no changes.
FR-020: a failed migration leaves the database in its previous working state.
FR-021: refuse to start against a schema stamped with a revision this code doesn't know.

spec 002 FR-002/FR-003: verify the intervals.icu credential at startup and identify the
bound athlete; refuse to start when it is absent, malformed, or rejected.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from filelock import FileLock, Timeout

from app.config import settings
from app.core.exceptions import (
    AnotherInstanceRunningError,
    MigrationFailedError,
    SchemaTooNewError,
)
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.errors import (
    CredentialRejectedError,
    IntervalsError,
)

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def acquire_instance_lock() -> FileLock:
    """An advisory lock held for the lifetime of the process, not a database row: a
    crashed instance releases it simply by dying (the OS reclaims the file descriptor),
    where a row-based lock would leave a stale marker indistinguishable from a live
    instance (spec 003 research R9). Refuses immediately (timeout=0) rather than
    blocking — a second instance starting up should fail fast and visibly, not hang."""
    lock_path = settings.data_dir / ".instance.lock"
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise AnotherInstanceRunningError(
            f"Another instance already holds the lock at '{lock_path}'. Only one "
            f"instance may run against the data directory at '{settings.data_dir}' at "
            f"a time — stop the other instance first."
        ) from exc
    return lock


def ensure_data_dir() -> None:
    """Create the data directory if missing, and confirm it is actually writable — spec
    001 requires every self-hosted deployment to reach a working state this way, and spec
    003 FR-004 requires the failure, if any, to name the directory and the problem rather
    than surfacing as an opaque database error later."""
    data_dir = settings.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create the data directory at '{data_dir}': {exc}. Check that the "
            f"parent directory exists and is writable by the process running this app."
        ) from exc

    probe = data_dir / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            f"The data directory at '{data_dir}' is not writable: {exc}. All athlete "
            f"data lives there — fix its permissions before starting the app."
        ) from exc


def _alembic_config() -> Config:
    cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # The application's settings are the single source of truth for the URL — matches
    # migrations/env.py's own resolution so both agree on what "the database" means.
    cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url)
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
            # this running version understands.
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
    """Bring the schema up to date. Call after ensure_data_dir() and
    acquire_instance_lock(), so the directory and the single-writer guarantee are both
    already in place before anything touches the schema."""
    logger.info("Checking database schema...")
    await asyncio.to_thread(_upgrade_to_head_sync)
    logger.info("Database schema is up to date.")


async def verify_intervals_credential() -> str:
    """Verify the intervals.icu API key at startup and identify the bound athlete
    (spec 002 FR-002). Returns the athlete id.

    Raises RuntimeError naming the setting and how to fix it (FR-003) rather than
    letting the raw client exception propagate — a 401 from a third-party API is not a
    message an operator should have to decode.
    """
    if not settings.intervals_api_key.get_secret_value():
        raise RuntimeError(
            "INTERVALS_API_KEY is not set. Get a personal API key from "
            "intervals.icu -> Settings -> Developer Settings, and set it in .env."
        )

    client = IntervalsClient(
        settings.intervals_api_key.get_secret_value(), athlete_id=settings.intervals_athlete_id
    )
    try:
        athlete = await client.get_athlete()
    except CredentialRejectedError as exc:
        raise RuntimeError(
            "INTERVALS_API_KEY was rejected by intervals.icu. Check that the key is "
            "correct and has not been revoked, in Settings -> Developer Settings."
        ) from exc
    except IntervalsError as exc:
        raise RuntimeError(
            f"Could not reach intervals.icu to verify the credential: {exc}. Check "
            f"network connectivity and try again."
        ) from exc

    athlete_id = athlete.get("id", "")
    logger.info("intervals.icu credential verified — bound to athlete %s", athlete_id)
    return athlete_id
