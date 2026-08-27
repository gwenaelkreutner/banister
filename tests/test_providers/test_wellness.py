"""Wellness ingestion tests (spec 002 T030, FR-023, FR-024).

The point: a missing wellness reading must be stored as unknown (None), never as 0 or
any other falsy stand-in — a `0` HRV would later read as a catastrophic drop once spec
006 evaluates readiness thresholds against it.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.db.models.user import User
from app.db.repositories import wellness_repo
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.wellness import ingest_wellness

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "intervals"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def patch_transport(monkeypatch):
    def _apply(handler):
        real_async_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)

    return _apply


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_missing_readings_stay_none_not_zero(db_session, patch_transport):
    wellness_payload = _load("wellness_range.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wellness_payload)

    patch_transport(handler)
    user = await _make_user(db_session, 400)
    client = IntervalsClient("test-key", athlete_id="i000000")

    count = await ingest_wellness(
        db_session, user.id, client, oldest="2026-08-20", newest="2026-08-27"
    )
    await db_session.commit()

    assert count == len(wellness_payload)

    from datetime import date

    record = await wellness_repo.get_by_date(db_session, user.id, date(2026, 8, 20))
    assert record is not None
    # Every one of these is `null` in the fixture for this day — must stay None, not 0.
    assert record.hrv is None
    assert record.resting_hr is None
    assert record.sleep_seconds is None
    assert record.weight_kg is None


async def test_real_ctl_atl_values_are_stored(db_session, patch_transport):
    wellness_payload = _load("wellness_range.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wellness_payload)

    patch_transport(handler)
    user = await _make_user(db_session, 401)
    client = IntervalsClient("test-key", athlete_id="i000000")

    await ingest_wellness(db_session, user.id, client, oldest="2026-08-20", newest="2026-08-27")
    await db_session.commit()

    from datetime import date

    record = await wellness_repo.get_by_date(db_session, user.id, date(2026, 8, 20))
    assert record.ctl == pytest.approx(39.852047)
    assert record.atl == pytest.approx(41.434277)


async def test_reingesting_the_same_day_updates_rather_than_duplicates(db_session, patch_transport):
    wellness_payload = _load("wellness_range.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wellness_payload)

    patch_transport(handler)
    user = await _make_user(db_session, 402)
    client = IntervalsClient("test-key", athlete_id="i000000")

    await ingest_wellness(db_session, user.id, client, oldest="2026-08-20", newest="2026-08-27")
    await db_session.commit()
    await ingest_wellness(db_session, user.id, client, oldest="2026-08-20", newest="2026-08-27")
    await db_session.commit()

    from datetime import date

    rows = await wellness_repo.get_range(db_session, user.id, date(2026, 8, 20), date(2026, 8, 27))
    dates = [r.date for r in rows]
    assert len(dates) == len(set(dates)) == len(wellness_payload)
