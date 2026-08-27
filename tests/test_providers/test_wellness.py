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


async def test_get_latest_returns_the_most_recent_row_with_ctl(db_session, patch_transport):
    wellness_payload = _load("wellness_range.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wellness_payload)

    patch_transport(handler)
    user = await _make_user(db_session, 403)
    client = IntervalsClient("test-key", athlete_id="i000000")

    await ingest_wellness(db_session, user.id, client, oldest="2026-08-20", newest="2026-08-27")
    await db_session.commit()

    from datetime import date

    latest = await wellness_repo.get_latest(db_session, user.id, on_or_before=date(2026, 8, 27))
    assert latest is not None
    assert latest.date == date(2026, 8, 27)


async def test_get_latest_falls_back_to_an_earlier_date_when_asked_for_the_future(db_session):
    """Simulates a poll tick that ran before intervals.icu finished computing today's CTL
    — the caller asks for `on_or_before=today` and must get yesterday's real value, not
    nothing and not today's absent one silently treated as zero."""
    from datetime import date

    user = await _make_user(db_session, 404)
    await wellness_repo.upsert(db_session, user.id, date(2026, 8, 26), ctl=45.5, atl=65.4)
    await db_session.commit()

    latest = await wellness_repo.get_latest(db_session, user.id, on_or_before=date(2026, 8, 27))
    assert latest is not None
    assert latest.date == date(2026, 8, 26)


async def test_get_latest_returns_none_when_no_wellness_exists(db_session):
    from datetime import date

    user = await _make_user(db_session, 405)
    latest = await wellness_repo.get_latest(db_session, user.id, on_or_before=date(2026, 8, 27))
    assert latest is None
