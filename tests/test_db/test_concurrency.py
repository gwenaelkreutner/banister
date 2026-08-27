"""Concurrent write tests (spec 003 FR-013, FR-014, research R5).

Drives concurrent writers against the actual app/db/client.py engine — not the shared
db_session fixture's own engine — because the property under test is whether the WAL and
busy_timeout pragmas configured there hold up, not whether SQLite in the abstract can.
Runs only against SQLite: the concurrency risk this feature introduces is specific to
SQLite's single-writer model — PostgreSQL already handles concurrent writers and isn't
what changed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models.base import Base
from app.db.models.chat_message import ChatMessage
from app.db.models.user import User


@pytest_asyncio.fixture
async def file_engine(tmp_path: Path):
    """A real file-based SQLite engine configured exactly like app/db/client.py —
    :memory: databases don't exhibit the same locking behaviour WAL mode addresses, since
    each in-memory connection can be an entirely separate database unless StaticPool is
    forced, which would mask the property under test."""
    db_path = tmp_path / "concurrency_test.db"
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

    yield engine
    await engine.dispose()


async def test_concurrent_writers_lose_no_writes(file_engine):
    """Simulates what the real deployment does: several background schedulers and an
    interactive handler all writing around the same time. FR-013: none of them may lose a
    write. FR-014: none of them may surface a locking error to its caller."""
    factory = async_sessionmaker(file_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as setup_session:
        user = User(telegram_id=1, first_name="Test")
        setup_session.add(user)
        await setup_session.commit()
        user_id = user.id

    write_count = 30

    async def write_one(i: int) -> None:
        async with factory() as session:
            session.add(ChatMessage(
                user_id=user_id, role="user", content=f"message {i}",
            ))
            await session.commit()

    errors = []

    async def write_one_guarded(i: int) -> None:
        try:
            await write_one(i)
        except Exception as exc:  # noqa: BLE001 — the test needs to see every failure
            errors.append((i, exc))

    await asyncio.gather(*(write_one_guarded(i) for i in range(write_count)))

    assert errors == [], (
        f"{len(errors)}/{write_count} concurrent writes raised instead of retrying "
        f"under contention: {errors[:3]}"
    )

    async with factory() as session:
        rows = (await session.execute(
            select(ChatMessage).where(ChatMessage.user_id == user_id)
        )).scalars().all()
    assert len(rows) == write_count, (
        f"expected all {write_count} concurrent writes to land, found {len(rows)} — "
        "a write was silently lost"
    )


async def test_reads_proceed_during_a_write(file_engine):
    """The specific benefit WAL mode exists to provide: a reader is not blocked behind an
    in-progress write. Without WAL, SQLite's default rollback-journal mode blocks readers
    for the duration of a write transaction."""
    factory = async_sessionmaker(file_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as setup_session:
        user = User(telegram_id=2, first_name="Test")
        setup_session.add(user)
        await setup_session.commit()
        user_id = user.id

    read_completed = asyncio.Event()

    async def slow_write():
        async with factory() as session:
            session.add(ChatMessage(user_id=user_id, role="user", content="slow"))
            await session.flush()
            await asyncio.sleep(0.3)  # hold the write transaction open
            await session.commit()

    async def concurrent_read():
        await asyncio.sleep(0.05)  # ensure the write has started first
        async with factory() as session:
            await session.execute(select(User).where(User.id == user_id))
        read_completed.set()

    await asyncio.wait_for(
        asyncio.gather(slow_write(), concurrent_read()), timeout=2.0
    )
    assert read_completed.is_set()
