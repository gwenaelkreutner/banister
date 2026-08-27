"""Portable column type tests (spec 003 research R2, R3).

test_naive_datetime_on_plain_column_loses_offset is the falsification test: it demonstrates
the failure UtcDateTime exists to prevent, using the same plain DateTime(timezone=True) the
rest of the codebase still uses. If this test starts passing, either SQLite's behaviour
changed or the test stopped exercising the real column — either way it needs attention.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UtcDateTime


class _ProbeBase(DeclarativeBase):
    pass


class _PlainTimestamp(_ProbeBase):
    __tablename__ = "probe_plain"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _UtcTimestamp(_ProbeBase):
    __tablename__ = "probe_utc"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)


@pytest.fixture
async def probe_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_ProbeBase.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_naive_datetime_on_plain_column_loses_offset(probe_session):
    """Falsification test: proves the failure this feature exists to prevent. A plain
    DateTime(timezone=True) column on SQLite discards the offset — this is research R2's
    finding, pinned as a regression test rather than left as a one-off observation."""
    written = datetime(2026, 8, 27, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    probe_session.add(_PlainTimestamp(id=1, ts=written))
    await probe_session.commit()
    probe_session.expire_all()

    reread = (await probe_session.get(_PlainTimestamp, 1)).ts

    assert reread.tzinfo is None, (
        "Plain DateTime(timezone=True) no longer loses tzinfo on this SQLite/SQLAlchemy "
        "version — UtcDateTime may no longer be necessary, but verify before removing it."
    )
    assert reread.replace(tzinfo=UTC) != written.astimezone(UTC), (
        "the naive value silently denotes a different instant than what was written"
    )


async def test_utc_datetime_preserves_the_instant(probe_session):
    written = datetime(2026, 8, 27, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    probe_session.add(_UtcTimestamp(id=1, ts=written))
    await probe_session.commit()
    probe_session.expire_all()

    reread = (await probe_session.get(_UtcTimestamp, 1)).ts

    assert reread.tzinfo is not None
    assert reread == written.astimezone(UTC)


async def test_utc_datetime_rejects_naive_input(probe_session):
    from sqlalchemy.exc import StatementError

    probe_session.add(_UtcTimestamp(id=1, ts=datetime(2026, 8, 27, 10, 0)))
    with pytest.raises(StatementError, match="naive datetime") as exc_info:
        await probe_session.commit()
    assert isinstance(exc_info.value.orig, ValueError)


async def test_utc_datetime_normalises_non_utc_offsets_consistently(probe_session):
    """Two instants that are equal but expressed in different offsets must compare equal
    after round-tripping, not merely have equal tzinfo."""
    plus_two = datetime(2026, 8, 27, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    utc = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    assert plus_two == utc  # sanity: same instant, different representation

    probe_session.add(_UtcTimestamp(id=1, ts=plus_two))
    probe_session.add(_UtcTimestamp(id=2, ts=utc))
    await probe_session.commit()
    probe_session.expire_all()

    r1 = (await probe_session.get(_UtcTimestamp, 1)).ts
    r2 = (await probe_session.get(_UtcTimestamp, 2)).ts
    assert r1 == r2 == utc
