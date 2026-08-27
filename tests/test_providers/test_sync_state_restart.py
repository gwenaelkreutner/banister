"""A reported marker must survive a restart (spec 002 T031, FR-009).

An in-memory-only implementation would silently pass every test that doesn't actually
close and reopen the store — this is the one that would catch it: the marker is written,
the connection is dropped entirely (simulating a process restart), and a fresh connection
must still see it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.base import Base
from app.db.models.user import User
from app.db.repositories import sync_state_repo


@pytest_asyncio.fixture
async def file_engine(tmp_path: Path):
    db_path = tmp_path / "sync_state_restart_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine, db_path
    await engine.dispose()


async def test_reported_marker_survives_a_restart(file_engine):
    engine, db_path = file_engine
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        user = User(telegram_id=500, first_name="RestartTest")
        session.add(user)
        await session.flush()
        user_id = user.id
        await sync_state_repo.mark_reported(
            session, user_id, "activity-123", reported_at=datetime.now(UTC)
        )
        await session.commit()

    await engine.dispose()  # drop every connection — simulates a process restart

    reopened = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        reopened_factory = async_sessionmaker(reopened, class_=AsyncSession)
        async with reopened_factory() as check_session:
            assert await sync_state_repo.is_reported(check_session, user_id, "activity-123")
            assert not await sync_state_repo.is_reported(check_session, user_id, "activity-999")
    finally:
        await reopened.dispose()


async def test_marking_the_same_activity_reported_twice_does_not_duplicate(file_engine):
    """FR-012's idempotence: a retry after a crash between write and confirmation must
    not raise or create a second row."""
    engine, _ = file_engine
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        user = User(telegram_id=501, first_name="Idempotent")
        session.add(user)
        await session.flush()

        first_time = datetime.now(UTC)
        await sync_state_repo.mark_reported(
            session, user.id, "activity-456", reported_at=first_time
        )
        await session.commit()

        await sync_state_repo.mark_reported(
            session, user.id, "activity-456", reported_at=datetime.now(UTC)
        )
        await session.commit()

        from sqlalchemy import func, select

        from app.db.models.sync_state import ReportedActivity

        count = await session.scalar(
            select(func.count()).select_from(ReportedActivity).where(
                ReportedActivity.user_id == user.id,
                ReportedActivity.source_activity_id == "activity-456",
            )
        )
        assert count == 1
