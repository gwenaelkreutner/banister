"""Fixtures for provider-layer tests. This project targets SQLite only (spec 003 Phase 7
cutover) — unlike tests/test_db/conftest.py's parametrized fixture (a leftover from the
PostgreSQL portability port), nothing here needs a second dialect.
"""
from __future__ import annotations

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
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
