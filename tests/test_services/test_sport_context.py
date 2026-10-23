"""app/services/sport_context.py + app/api/internal.py — read-only sport context for the
Hermes mobility coach (replaces its direct read of Banister's SQLite)."""
from __future__ import annotations

from datetime import date, timedelta

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.internal import router
from app.config import settings
from app.db.client import get_session
from app.db.models.user import User
from app.db.repositories import (
    freestyle_publication_repo,
    plan_repo,
    session_log_repo,
    wellness_repo,
)
from app.engine.plan_builder import generate_plan
from app.services.sport_context import build_sport_context
from tests.test_engine.test_plan_builder import make_profile


async def _make_user(session) -> User:
    user = User(telegram_id=4242, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_empty_athlete_is_explicit_not_invented(db_session):
    user = await _make_user(db_session)
    ctx = await build_sport_context(db_session, user.id, today=date(2026, 10, 23))
    assert ctx["fitness"] is None
    assert ctx["recovery"] == {"available": False}
    assert ctx["recent_rides"] == [] and ctx["planned"] == []
    assert ctx["coaching_mode"] == "freestyle"
    assert ctx["recovery_unknown_reason"]  # silence must not read as "recovery OK"


async def test_fitness_recovery_and_recent_rides(db_session):
    user = await _make_user(db_session)
    today = date(2026, 10, 23)
    for i, hrv in enumerate([60, 62, 58, 61, 59, 60, 63]):
        await wellness_repo.upsert(db_session, user.id, today - timedelta(days=7 - i),
                                   hrv=hrv, resting_hr=48, ctl=40.0, atl=45.0)
    await wellness_repo.upsert(db_session, user.id, today, hrv=50, resting_hr=52,
                               sleep_seconds=6 * 3600, ctl=41.04, atl=52.0, ramp_rate=3.2,
                               fatigue=3)
    await session_log_repo.create(db_session, user.id, None, None, None, today - timedelta(days=1),
                                  "unplanned", rpe=7, duration_minutes_actual=180, tss_actual=148.4,
                                  session_type_real="long_ride")
    await session_log_repo.create(db_session, user.id, None, None, None, today - timedelta(days=9),
                                  "unplanned", duration_minutes_actual=60, tss_actual=50)
    await session_log_repo.create(db_session, user.id, None, None, None, today - timedelta(days=2),
                                  "skipped")
    await db_session.commit()

    ctx = await build_sport_context(db_session, user.id, today=today, days=7)

    assert ctx["fitness"] == {"date": "2026-10-23", "stale": False, "ctl": 41.0, "atl": 52.0,
                              "tsb": round(41.04 - 52.0, 1), "ramp_rate": 3.2}
    rec = ctx["recovery"]
    assert rec["date"] == "2026-10-23" and rec["hrv"] == 50 and rec["hrv_avg_7d"] == 60.4
    assert rec["sleep_hours"] == 6.0 and rec["fatigue"] == 3
    # skipped and out-of-window sessions excluded; TSS taken as stored
    assert ctx["recent_rides"] == [{"date": "2026-10-22", "type": "long_ride", "duration_min": 180,
                                    "tss": 148.0, "rpe": 7, "planned": False}]
    assert ctx["recent_tss"] == 148.0
    for f in ctx["guardrails"]:
        assert f["action"] and f["kind"]


async def test_planned_sessions_from_plan_and_freestyle(db_session):
    user = await _make_user(db_session)
    schema = generate_plan(make_profile())
    start = schema.weeks[0].start_date
    await plan_repo.create(db_session, user.id, plan_technical=schema.model_dump(mode="json"),
                           start_date=start, end_date=start + timedelta(weeks=12))
    await freestyle_publication_repo.create(
        db_session, user_id=user.id, external_id="banister:freestyle:x", intervals_event_id="1",
        session_date=start + timedelta(days=3), workout_type="endurance", content_hash="h")
    await db_session.commit()

    ctx = await build_sport_context(db_session, user.id, today=start, ahead=7)

    assert ctx["coaching_mode"] == "goal"
    dates = [s["date"] for s in ctx["planned"]]
    assert dates == sorted(dates)
    assert all(start.isoformat() <= d <= (start + timedelta(days=6)).isoformat() for d in dates)
    assert {"plan", "freestyle"} == {s["source"] for s in ctx["planned"]}
    plan_session = next(s for s in ctx["planned"] if s["source"] == "plan")
    assert plan_session["workout_type"] and plan_session["duration_min"]


def _client(db_session) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)

    async def _session():
        yield db_session

    app.dependency_overrides[get_session] = _session
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_endpoint_disabled_without_token(db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", SecretStr(""))
    async with _client(db_session) as c:
        r = await c.get("/internal/v1/context", headers={"Authorization": "Bearer "})
    assert r.status_code == 404


async def test_endpoint_requires_matching_bearer_token(db_session, monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", SecretStr("s3cret-token"))
    await _make_user(db_session)
    await db_session.commit()
    async with _client(db_session) as c:
        assert (await c.get("/internal/v1/context")).status_code == 401
        assert (await c.get("/internal/v1/context",
                            headers={"Authorization": "Bearer wrong"})).status_code == 401
        r = await c.get("/internal/v1/context?days=3&ahead=2",
                        headers={"Authorization": "Bearer s3cret-token"})
        assert (await c.get("/internal/v1/context?days=999",
                            headers={"Authorization": "Bearer s3cret-token"})).status_code == 422
    assert r.status_code == 200
    body = r.json()
    assert body["recent_days"] == 3
    expected = {"fitness", "recovery", "guardrails", "recent_rides", "planned", "profile"}
    assert expected <= body.keys()
