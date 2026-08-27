"""Durability test (spec 003 FR-015): an abrupt termination must not corrupt the store or
leave a partially written record visible.

A real process-kill test belongs in quickstart.md's manual scenario 5 (SIGKILL against a
running container) — this suite cannot substitute for that, since killing this test's own
process would also kill the test. What it can exercise from inside a single process is the
other half of the same guarantee: an abandoned, never-committed write must leave no trace,
and the store must open without error afterward — both properties WAL mode plus
synchronous=NORMAL are meant to hold.
"""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.base import Base
from app.db.models.user import User


@pytest_asyncio.fixture
async def file_engine(tmp_path: Path):
    db_path = tmp_path / "durability_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine, db_path
    await engine.dispose()


async def test_an_uncommitted_write_is_not_visible_after_reconnecting(file_engine):
    """The other half of the same guarantee: a write that was never committed must not
    appear after reconnecting, even though the process holding it "crashed" (here:
    the session is simply abandoned without commit or rollback)."""
    engine, db_path = file_engine
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    session = factory()
    user = User(telegram_id=301, first_name="NeverCommitted")
    session.add(user)
    await session.flush()  # visible within the transaction, not yet committed
    uncommitted_id = user.id
    await session.close()  # abandoned without commit — simulates a crash mid-write

    reopened = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        reopened_factory = async_sessionmaker(reopened, class_=AsyncSession)
        async with reopened_factory() as check_session:
            found = await check_session.get(User, uncommitted_id)
            assert found is None, (
                "an uncommitted write survived an abandoned session — a crash "
                "mid-write would leave a partial record visible"
            )
    finally:
        await reopened.dispose()


async def test_database_opens_intact_after_reconnecting(file_engine):
    """FR-015 stated directly: the store opens without error after the disruption
    exercised above, rather than reporting corruption."""
    engine, db_path = file_engine
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(User(telegram_id=302, first_name="Check"))
        await session.commit()

    reopened = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        async with reopened.connect() as conn:
            result = await conn.execute(select(User.telegram_id))
            telegram_ids = {row[0] for row in result}
        assert 302 in telegram_ids
    except DBAPIError as exc:
        raise AssertionError(f"database did not open cleanly after reconnect: {exc}") from exc
    finally:
        await reopened.dispose()
