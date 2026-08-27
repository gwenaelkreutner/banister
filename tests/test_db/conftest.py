"""Fixtures for the storage-engine portability tests (spec 003).

Every fixture that touches a database is exposed twice: once bound to SQLite (always
available, in-memory) and once bound to PostgreSQL (only if a database is reachable at
``TEST_DATABASE_URL`` — the point of Phase 2/3 is to validate portability changes while
still running the real target of the pre-migration test suite).

When PostgreSQL is not reachable, the postgres-parametrized tests are skipped rather than
failed, and the skip reason states why. A green Phase 2/3 run therefore does not by itself
prove the PostgreSQL half — check the skip report.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://banister:password@localhost:5432/banister_test",
)


async def _postgres_reachable() -> bool:
    try:
        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def db_session(request) -> AsyncSession:
    """A session against each backend under test. Skips the postgres param if unreachable."""
    from app.db.models.base import Base

    if request.param == "postgres":
        if not await _postgres_reachable():
            pytest.skip(
                f"PostgreSQL not reachable at {TEST_DATABASE_URL} — set TEST_DATABASE_URL "
                "or start the db service to run this half of the portability check."
            )
        url = TEST_DATABASE_URL
    else:
        url = "sqlite+aiosqlite:///:memory:"

    engine = create_async_engine(url)

    if request.param == "sqlite":
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def sqlite_session() -> AsyncSession:
    """SQLite-only session, for tests specific to that backend (e.g. pragma verification)."""
    from sqlalchemy import event

    from app.db.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def sqlite_session_no_fk() -> AsyncSession:
    """SQLite session WITHOUT the foreign-key pragma — used to prove a cascade test is real
    by confirming it fails here (see tasks.md T034)."""
    from app.db.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
