"""Agent API (app/api/agent.py): access, session design, approval, reply check."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.agent import intervals_client, router
from app.config import settings
from app.core.time import paris_today
from app.db.client import get_session
from app.db.models.user import User
from app.db.repositories import freestyle_publication_repo, profile_repo, wellness_repo
from tests.fake_intervals import FakeCalendarClient
from tests.test_engine.test_plan_builder import make_profile

TOKEN = "s3cret"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
STEADY = [{"kind": "steady", "duration_minutes": 180, "zone_code": "Z2"}]


@pytest_asyncio.fixture
async def db_session():
    from app.db.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def calendar():
    return FakeCalendarClient()


@pytest_asyncio.fixture
async def athlete(db_session):
    user = User(telegram_id=4242, first_name="Test")
    db_session.add(user)
    await db_session.flush()
    await profile_repo.create(db_session, user.id, make_profile(ftp=250).model_dump(mode="json"))
    await db_session.commit()
    return user


@pytest.fixture
def client(db_session, calendar, monkeypatch):
    monkeypatch.setattr(settings, "agent_api_token", SecretStr(TOKEN))
    app = FastAPI()
    app.include_router(router)

    async def _session():
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[intervals_client] = lambda: calendar
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=HEADERS
    )


def _tomorrow() -> str:
    return (paris_today() + timedelta(days=1)).isoformat()


async def test_disabled_without_token_and_refuses_a_bad_one(client, monkeypatch):
    assert (
        await client.get("/agent/v1/sessions/catalog", headers={"Authorization": "Bearer x"})
    ).status_code == 401
    monkeypatch.setattr(settings, "agent_api_token", SecretStr(""))
    assert (await client.get("/agent/v1/sessions/catalog")).status_code == 404


async def test_catalog_returns_several_candidates_and_filters(client):
    everything = (await client.get("/agent/v1/sessions/catalog")).json()
    assert everything["count"] >= 10
    assert "structure" not in everything["templates"][0]

    intervals = (
        await client.get(
            "/agent/v1/sessions/catalog", params={"workout_type": "intervals", "detail": "true"}
        )
    ).json()
    assert intervals["count"] > 1
    assert all(t["workout_type"] == "intervals" and t["structure"] for t in intervals["templates"])

    searched = (await client.get("/agent/v1/sessions/catalog", params={"q": "threshold"})).json()
    assert 0 < searched["count"] < everything["count"]


async def test_evaluate_composed_session_computes_load_and_targets(client, athlete, calendar):
    r = await client.post(
        "/agent/v1/sessions/evaluate",
        json={
            "date": _tomorrow(),
            "steps": STEADY,
            "workout_type": "endurance",
            "title": "Group ride",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Group ride" and body["date"] == _tomorrow()
    assert body["evaluated_as_of"] == paris_today().isoformat()
    assert body["duration_minutes"] == 180 and body["zone_code"] == "Z2"
    assert body["tss_estimate"] > 0
    assert body["zone_targets"]["Z2"]["watts"] == [137, 187]
    assert body["warnings"][0]["code"] == "no_fitness_data"  # unknown, not assumed fine
    assert calendar.create_calls == 0


async def test_evaluate_template_fitted_to_a_duration(client, athlete):
    catalog = (
        await client.get("/agent/v1/sessions/catalog", params={"workout_type": "endurance"})
    ).json()["templates"]
    scalable = next(t for t in catalog if t["scalable"])
    r = await client.post(
        "/agent/v1/sessions/evaluate",
        json={
            "date": _tomorrow(),
            "template_id": scalable["id"],
            "duration_minutes": 90,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["template_id"] == scalable["id"] and r.json()["duration_minutes"] == 90


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": STEADY},  # no workout_type
        {"template_id": "x", "steps": STEADY, "workout_type": "endurance"},  # both sources
        {},  # neither
        {"template_id": "does-not-exist"},
        {
            "steps": [{"kind": "steady", "duration_minutes": 60, "zone_code": "Z9"}],
            "workout_type": "endurance",
        },
        {
            "steps": [{"kind": "steady", "duration_minutes": 601, "zone_code": "Z2"}],
            "workout_type": "endurance",
        },
    ],
)
async def test_evaluate_refuses_a_session_that_cannot_exist(client, athlete, payload):
    r = await client.post("/agent/v1/sessions/evaluate", json={"date": _tomorrow(), **payload})
    assert r.status_code == 422


@pytest.mark.parametrize("offset", [-1, 29])
async def test_evaluate_refuses_dates_out_of_range(client, athlete, offset):
    day = (paris_today() + timedelta(days=offset)).isoformat()
    r = await client.post(
        "/agent/v1/sessions/evaluate",
        json={
            "date": day,
            "steps": STEADY,
            "workout_type": "endurance",
        },
    )
    assert r.status_code == 422


async def test_high_load_is_reported_never_blocked(client, athlete, db_session):
    today = paris_today()
    await wellness_repo.upsert(db_session, athlete.id, today, ctl=30.0, atl=30.0)
    await db_session.commit()
    r = await client.post(
        "/agent/v1/sessions/evaluate",
        json={
            "date": _tomorrow(),
            "steps": [{"kind": "steady", "duration_minutes": 360, "zone_code": "Z3"}],
            "workout_type": "long_ride",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine_reference"] and body["fitness"]["ctl"] == 30.0
    assert "above_reference_load" in {w["code"] for w in body["warnings"]}
    assert (
        await client.post(
            "/agent/v1/proposals",
            json={
                "date": _tomorrow(),
                "steps": [{"kind": "steady", "duration_minutes": 360, "zone_code": "Z3"}],
                "workout_type": "long_ride",
            },
        )
    ).status_code == 201


async def _propose(client) -> dict:
    r = await client.post(
        "/agent/v1/proposals",
        json={
            "date": _tomorrow(),
            "steps": STEADY,
            "workout_type": "endurance",
            "title": "Group ride",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_proposing_writes_nothing_and_the_card_states_computed_values(
    client, athlete, calendar
):
    proposal = await _propose(client)
    assert calendar.create_calls == 0
    assert proposal["proposal_id"] and proposal["card_buttons"]["approve"]
    assert "Group ride" in proposal["card_text"] and "137" in proposal["card_text"]
    assert (await client.get(f"/agent/v1/proposals/{proposal['proposal_id']}")).json()[
        "status"
    ] == "pending"


async def test_approve_publishes_on_the_proposed_date_exactly_once(
    client, athlete, calendar, db_session
):
    proposal = await _propose(client)
    url = f"/agent/v1/proposals/{proposal['proposal_id']}/approve"

    first = await client.post(url)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "published" and first.json()["text"]
    assert calendar.create_calls == 1

    again = await client.post(url)
    assert again.status_code == 200 and again.json()["already_done"] is True
    assert calendar.create_calls == 1

    active = await freestyle_publication_repo.get_active_for_user(db_session, athlete.id)
    assert [str(e.id) for e in active] == [first.json()["publication_id"]]
    assert active[0].session_date.isoformat() == _tomorrow()


async def test_failed_write_stays_retryable(client, athlete, calendar, monkeypatch):
    proposal = await _propose(client)
    url = f"/agent/v1/proposals/{proposal['proposal_id']}/approve"

    async def boom(_payload):
        raise RuntimeError("intervals down")

    with monkeypatch.context() as patch:
        patch.setattr(calendar, "create_event", boom)
        r = await client.post(url)
    assert r.status_code == 502
    assert (await client.get(f"/agent/v1/proposals/{proposal['proposal_id']}")).json()[
        "status"
    ] == "pending"

    assert (await client.post(url)).status_code == 200
    assert calendar.create_calls == 1


async def test_declined_or_unknown_proposal_cannot_be_approved(client, athlete, calendar):
    proposal = await _propose(client)
    base = f"/agent/v1/proposals/{proposal['proposal_id']}"
    assert (await client.post(f"{base}/decline")).status_code == 200
    assert (await client.post(f"{base}/approve")).status_code == 409
    assert (await client.post("/agent/v1/proposals/nope/approve")).status_code == 404
    assert calendar.create_calls == 0


async def test_past_proposal_cannot_be_approved(client, athlete, calendar, db_session):
    from app.db.repositories import proposal_repo

    proposal = await _propose(client)
    row = await proposal_repo.get(db_session, athlete.id, proposal["proposal_id"])
    day = paris_today() - timedelta(days=1)
    row.session_date = day
    await db_session.commit()
    assert (await client.post(f"/agent/v1/proposals/{row.id}/approve")).status_code == 409
    assert calendar.create_calls == 0


async def test_no_threshold_reference_is_unknown_not_a_default(client, db_session, calendar):
    user = User(telegram_id=1, first_name="NoFtp")
    db_session.add(user)
    await db_session.flush()
    data = make_profile(ftp=250).model_dump(mode="json")
    data["equipment"]["ftp"] = None
    await profile_repo.create(db_session, user.id, data)
    await db_session.commit()

    payload = {"date": _tomorrow(), "steps": STEADY, "workout_type": "endurance"}
    body = (await client.post("/agent/v1/sessions/evaluate", json=payload)).json()
    assert body["publishable"] is False and body["zone_targets"] == {}
    assert "no_threshold_reference" in {w["code"] for w in body["warnings"]}
    assert (await client.post("/agent/v1/proposals", json=payload)).status_code == 409


async def test_withdraw_a_publication(client, athlete, calendar, db_session):
    proposal = await _propose(client)
    published = (await client.post(f"/agent/v1/proposals/{proposal['proposal_id']}/approve")).json()
    pid = published["publication_id"]

    listed = (await client.get("/agent/v1/publications")).json()["publications"]
    assert [p["publication_id"] for p in listed] == [pid]
    card = (await client.get(f"/agent/v1/publications/{pid}/withdrawal-card")).json()
    assert card["card_text"] and calendar.delete_calls == 0

    assert (await client.delete(f"/agent/v1/publications/{pid}")).status_code == 200
    assert calendar.delete_calls == 1
    assert (await client.get("/agent/v1/publications")).json()["publications"] == []
    assert (await client.delete(f"/agent/v1/publications/{pid}")).status_code == 404


async def test_memory_note_is_kept_and_found(client, athlete):
    r = await client.post(
        "/agent/v1/memory/notes", json={"category": "preference", "note": "Loves gravel"}
    )
    assert r.status_code == 201, r.text
    found = (await client.get("/agent/v1/memory", params={"keyword": "gravel"})).json()
    assert found["journal"]["count"] == 1


async def test_verify_reply_withholds_invented_numbers_and_records_them(
    client, athlete, db_session
):
    from sqlalchemy import select

    from app.db.models.guardrail import ResponseCheckFailure

    await wellness_repo.upsert(db_session, athlete.id, paris_today(), ctl=41.0, atl=52.0)
    await db_session.commit()

    ok = await client.post("/agent/v1/verify-reply", json={"text": "Ta CTL est à 41 aujourd'hui."})
    assert ok.json() == {"ok": True, "text": "Ta CTL est à 41 aujourd'hui."}

    bad = await client.post(
        "/agent/v1/verify-reply", json={"text": "Ta CTL est à 70. Roule tranquille."}
    )
    assert bad.json()["ok"] is False
    assert "70" not in bad.json()["text"] and "Roule tranquille." in bad.json()["text"]
    failures = (await db_session.execute(select(ResponseCheckFailure))).scalars().all()
    assert [f.metric_name for f in failures] == ["ctl"]


async def test_verify_reply_accepts_what_the_agent_retrieved(client, athlete):
    text = "Cette séance fait environ 140 TSS."
    assert (await client.post("/agent/v1/verify-reply", json={"text": text})).json()["ok"] is False
    good = await client.post(
        "/agent/v1/verify-reply",
        json={
            "text": text,
            "retrieved": [{"kind": "session", "result": {"tss_estimate": 140.0}}],
        },
    )
    assert good.json()["ok"] is True
