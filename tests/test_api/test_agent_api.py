"""Agent API (app/api/agent.py): access, session design, approval, reply check."""

from __future__ import annotations

from datetime import date, timedelta

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
    monkeypatch.setattr("app.api.agent.messages.intervals_client", lambda: calendar)
    app.dependency_overrides[intervals_client] = lambda: calendar
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=HEADERS
    )


async def _tap(client, action: str):
    return await client.post("/agent/v1/actions", json={"action": action})


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
    assert proposal["proposal_id"] and proposal["card"]["buttons"]
    assert "Group ride" in proposal["card"]["text"] and "137" in proposal["card"]["text"]
    assert (await client.get(f"/agent/v1/proposals/{proposal['proposal_id']}")).json()[
        "status"
    ] == "pending"


async def test_approve_publishes_on_the_proposed_date_exactly_once(
    client, athlete, calendar, db_session
):
    proposal = await _propose(client)
    url = f"proposal:approve:{proposal['proposal_id']}"

    first = await _tap(client, url)
    assert first.status_code == 200, first.text
    assert first.json()["text"]
    assert calendar.create_calls == 1

    again = await _tap(client, url)
    assert again.status_code == 200
    assert calendar.create_calls == 1

    active = await freestyle_publication_repo.get_active_for_user(db_session, athlete.id)
    assert len(active) == 1
    assert active[0].session_date.isoformat() == _tomorrow()


async def test_failed_write_stays_retryable(client, athlete, calendar, monkeypatch):
    proposal = await _propose(client)
    url = f"proposal:approve:{proposal['proposal_id']}"

    async def boom(_payload):
        raise RuntimeError("intervals down")

    with monkeypatch.context() as patch:
        patch.setattr(calendar, "create_event", boom)
        r = await _tap(client, url)
    assert r.status_code == 502
    assert (await client.get(f"/agent/v1/proposals/{proposal['proposal_id']}")).json()[
        "status"
    ] == "pending"

    assert (await _tap(client, url)).status_code == 200
    assert calendar.create_calls == 1


async def test_declined_or_unknown_proposal_cannot_be_approved(client, athlete, calendar):
    proposal = await _propose(client)
    pid = proposal["proposal_id"]
    assert (await _tap(client, f"proposal:decline:{pid}")).status_code == 200
    assert (await _tap(client, f"proposal:approve:{pid}")).status_code == 409
    assert (await _tap(client, "proposal:approve:nope")).status_code == 404
    assert calendar.create_calls == 0


async def test_past_proposal_cannot_be_approved(client, athlete, calendar, db_session):
    from app.db.repositories import proposal_repo

    proposal = await _propose(client)
    row = await proposal_repo.get(db_session, athlete.id, proposal["proposal_id"])
    day = paris_today() - timedelta(days=1)
    row.session_date = day
    await db_session.commit()
    assert (await _tap(client, f"proposal:approve:{row.id}")).status_code == 409
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
    assert (await _tap(client, f"proposal:approve:{proposal['proposal_id']}")).status_code == 200

    listed = (await client.get("/agent/v1/publications")).json()["publications"]
    assert len(listed) == 1
    pid = listed[0]["publication_id"]
    card = (await client.get(f"/agent/v1/publications/{pid}/withdrawal-card")).json()
    assert card["card"]["text"] and calendar.delete_calls == 0

    assert (await _tap(client, f"publication:withdraw:{pid}")).status_code == 200
    assert calendar.delete_calls == 1
    assert (await client.get("/agent/v1/publications")).json()["publications"] == []
    assert (await _tap(client, f"publication:withdraw:{pid}")).status_code == 404


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


# ── Interface agent ──────────────────────────────────────────────────────────


async def test_outbox_carries_the_same_message_and_buttons_in_order(
    client, db_session, monkeypatch
):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from app.services.outbox import OutboxMessenger

    factory = async_sessionmaker(db_session.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.client.AsyncSessionFactory", factory)
    messenger = OutboxMessenger()
    await messenger.send_chat_action(1, "typing")
    await messenger.send_message(1, "first", parse_mode="HTML", disable_notification=True)
    await messenger.send_message(
        1,
        "second",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="7", callback_data="log:rpe:x:7")]]
        ),
        agent_prompt="follow up",
    )
    messages = (await client.get("/agent/v1/messages/pending")).json()["messages"]
    assert [m["text"] for m in messages] == ["first", "second"]
    assert messages[0]["silent"] is True and messages[0]["parse_mode"] == "HTML"
    assert messages[1]["buttons"] == [[{"label": "7", "action": "log:rpe:x:7"}]]
    assert messages[1]["agent_prompt"] == "follow up"

    mid = messages[0]["id"]
    assert (await client.post(f"/agent/v1/messages/{mid}/delivered")).json() == {"updated": True}
    assert (await client.post(f"/agent/v1/messages/{mid}/delivered")).json() == {"updated": False}
    left = (await client.get("/agent/v1/messages/pending")).json()["messages"]
    assert [m["text"] for m in left] == ["second"]


async def test_stale_messages_are_not_delivered(client, db_session):
    from datetime import UTC, datetime

    from app.services.outbox import MAX_AGE, enqueue

    old = await enqueue(db_session, text="yesterday's reminder")
    old.created_at = datetime.now(UTC) - MAX_AGE - timedelta(minutes=1)
    await db_session.commit()
    assert (await client.get("/agent/v1/messages/pending")).json()["messages"] == []


async def _logged_ride(db_session, athlete):
    from app.db.models.session_log import SessionLog

    log = SessionLog(
        user_id=athlete.id,
        logged_date=paris_today(),
        status="done",
        duration_minutes_actual=60,
        tss_actual=50.0,
        source_activity_id="i1",
        source="intervals_icu",
    )
    db_session.add(log)
    await db_session.commit()
    return log


async def test_rpe_tap_records_syncs_and_asks_the_agent_to_review(
    client, athlete, calendar, db_session
):
    log = await _logged_ride(db_session, athlete)
    synced = []

    async def update_activity_rpe(activity_id, rpe):
        synced.append((activity_id, rpe))

    calendar.update_activity_rpe = update_activity_rpe
    r = await _tap(client, f"log:rpe:{log.id}:7")
    assert r.status_code == 200, r.text
    assert log.rpe == 7.0 and synced == [("i1", 7)]
    prompt = r.json()["agent_prompt"]
    assert log.logged_date.isoformat() in prompt and "banister_ride" in prompt
    assert r.json()["handover_hint"]


async def test_rpe_skip_writes_nothing_but_still_asks_for_a_review(client, athlete, db_session):
    log = await _logged_ride(db_session, athlete)
    r = await _tap(client, f"log:rpe:{log.id}:skip")
    assert r.status_code == 200 and log.rpe is None and r.json()["agent_prompt"]


async def test_rpe_tap_refuses_bad_input(client, athlete, db_session):
    log = await _logged_ride(db_session, athlete)
    assert (await _tap(client, f"log:rpe:{log.id}:11")).status_code == 422
    assert (await _tap(client, "log:rpe:not-a-uuid:5")).status_code == 422
    assert (await _tap(client, f"log:rpe:{athlete.id}:5")).status_code == 404
    assert log.rpe is None


async def test_unknown_actions_do_nothing(client, athlete):
    for action in ("nope", "proposal:drop:x", "dismiss:app.secret", "calendar:approve:not-a-uuid"):
        assert (await _tap(client, action)).status_code == 422


async def test_ride_returns_the_review_facts_without_any_llm(client, athlete, db_session, calendar):
    log = await _logged_ride(db_session, athlete)

    async def get_activity(_id):
        return {"icu_rpe": 6}

    async def get_activity_streams(_id, types):
        return []

    calendar.get_activity = get_activity
    calendar.get_activity_streams = get_activity_streams
    monkey = await client.get(f"/agent/v1/rides/{log.logged_date.isoformat()}")
    assert monkey.status_code == 200, monkey.text
    review = monkey.json()["reviews"][0]
    assert review["ride"]["tss_actual"] == 50.0 and review["ride"]["rpe"] == 6
    assert review["coaching_mode"] == "freestyle"


async def test_recap_gives_figures_without_narration(client, athlete, db_session, monkeypatch):
    async def no_llm(*_a, **_k):
        raise AssertionError("no LLM in the agent recap")

    monkeypatch.setattr("app.services.weekly_recap._generate_coach_section", no_llm)
    monkeypatch.setattr("app.services.weekly_recap._generate_nextweek_section", no_llm)
    await _logged_ride(db_session, athlete)
    body = (await client.get("/agent/v1/recap")).json()
    assert body["has_data"] is True and body["text"]


async def test_a_ride_within_recent_habits_is_not_flagged_as_above_reference(
    client, athlete, db_session
):
    await wellness_repo.upsert(db_session, athlete.id, paris_today(), ctl=30.0, atl=30.0)
    await _logged_ride(db_session, athlete)
    from app.db.repositories import session_log_repo

    log = (await session_log_repo.get_by_date(db_session, athlete.id, paris_today()))[0]
    log.tss_actual = 200.0
    await db_session.commit()
    r = await client.post(
        "/agent/v1/sessions/evaluate",
        json={
            "date": _tomorrow(),
            "steps": STEADY,
            "workout_type": "endurance",
        },
    )
    body = r.json()
    assert body["engine_reference"]["recent_peak_day"]["tss"] == 200.0
    assert "above_reference_load" not in {w["code"] for w in body["warnings"]}


# ── Plan ─────────────────────────────────────────────────────────────────────


async def test_no_plan_reads_as_freestyle(client, athlete):
    assert (await client.get("/agent/v1/plan")).json() == {"mode": "freestyle", "plan": None}
    assert (await client.post("/agent/v1/plan/proposals/leave")).status_code == 409
    assert (await client.post("/agent/v1/plan/calendar/publish-request")).status_code == 409
    assert (
        await client.post(
            "/agent/v1/plan/proposals/week",
            json={
                "reason": "fatigue",
                "modification_type": "reduce_volume",
                "week_offset": 0,
            },
        )
    ).status_code == 409


async def _start_plan(client) -> dict:
    target = (paris_today() + timedelta(weeks=12)).isoformat()
    r = await client.post(
        "/agent/v1/plan/proposals/new", json={"goal": "event", "target_date": target}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_plan_only_starts_on_the_athletes_tap(client, athlete):
    proposal = await _start_plan(client)
    assert proposal["weeks"] > 0 and proposal["card"]["text"]
    assert (await client.get("/agent/v1/plan")).json()["mode"] == "freestyle"

    r = await _tap(client, f"change:apply:{proposal['proposal_id']}")
    assert r.status_code == 200, r.text
    view = (await client.get("/agent/v1/plan")).json()
    assert view["mode"] == "goal" and view["week"]["sessions_by_date"]
    assert (await _tap(client, f"change:apply:{proposal['proposal_id']}")).status_code == 409


async def test_cancel_leaves_everything_as_it_was(client, athlete):
    proposal = await _start_plan(client)
    assert (await _tap(client, f"change:cancel:{proposal['proposal_id']}")).status_code == 200
    assert (await client.get("/agent/v1/plan")).json()["mode"] == "freestyle"


async def test_a_proposal_goes_stale_when_the_plan_moved(client, athlete):
    first, second = await _start_plan(client), await _start_plan(client)
    assert (await _tap(client, f"change:apply:{first['proposal_id']}")).status_code == 200
    assert (await _tap(client, f"change:apply:{second['proposal_id']}")).status_code == 409


async def test_week_change_round_trip_applies(client, athlete):
    plan = await _start_plan(client)
    await _tap(client, f"change:apply:{plan['proposal_id']}")
    r = await client.post(
        "/agent/v1/plan/proposals/week",
        json={
            "reason": "fatigue",
            "modification_type": "reduce_volume",
            "week_offset": 0,
        },
    )
    assert r.status_code == 201, r.text
    before = (await client.get("/agent/v1/plan")).json()["week"]["total_tss_target"]
    assert (await _tap(client, f"change:apply:{r.json()['proposal_id']}")).status_code == 200
    assert (await client.get("/agent/v1/plan")).json()["week"]["total_tss_target"] < before


async def test_session_change_stored_as_json_still_applies(client, athlete):
    plan = await _start_plan(client)
    await _tap(client, f"change:apply:{plan['proposal_id']}")
    view = (await client.get("/agent/v1/plan")).json()
    dates = sorted(d for d in view["week"]["sessions_by_date"] if d >= paris_today().isoformat())
    if not dates or (date.fromisoformat(dates[0]) - paris_today()).days > 6:
        pytest.skip("no session in the coming days of this plan")
    r = await client.post(
        "/agent/v1/plan/proposals/session",
        json={
            "constraint_type": "personal",
            "day_offset": (date.fromisoformat(dates[0]) - paris_today()).days,
            "action": "reduce_50",
        },
    )
    assert r.status_code == 201, r.text
    assert (await _tap(client, f"change:apply:{r.json()['proposal_id']}")).status_code == 200


async def test_plan_publication_goes_through_the_recorded_approval(client, athlete, calendar):
    plan = await _start_plan(client)
    await _tap(client, f"change:apply:{plan['proposal_id']}")
    r = await client.post("/agent/v1/plan/calendar/publish-request")
    assert r.status_code == 201, r.text
    assert calendar.create_calls == 0
    action = r.json()["card"]["buttons"][0][0]["action"]
    assert (await _tap(client, action)).status_code == 200
    assert calendar.create_calls == r.json()["session_count"]
    assert (await _tap(client, action)).status_code == 409

    w = await client.post("/agent/v1/plan/calendar/withdraw-request")
    assert w.status_code == 200, w.text
    assert (await _tap(client, "calendar:withdraw_all")).status_code == 200
    assert calendar.delete_calls == r.json()["session_count"]


async def test_leaving_the_plan_on_a_tap(client, athlete):
    plan = await _start_plan(client)
    await _tap(client, f"change:apply:{plan['proposal_id']}")
    leave = (await client.post("/agent/v1/plan/proposals/leave")).json()
    assert (await client.get("/agent/v1/plan")).json()["mode"] == "goal"
    assert (await _tap(client, f"change:apply:{leave['proposal_id']}")).status_code == 200
    assert (await client.get("/agent/v1/plan")).json()["mode"] == "freestyle"


async def test_injury_and_reminders(client, athlete):
    r = await client.put(
        "/agent/v1/injury",
        json={
            "location": "knee",
            "severity": "moderate",
            "estimated_recovery_days": 10,
        },
    )
    assert r.status_code == 200 and r.json()["zone_restrictions"]
    r = await client.put("/agent/v1/reminders", json={"enabled": True, "hour": 6, "minute": 30})
    assert r.json() == {"enabled": True, "hour": 6, "minute": 30}
    assert (await client.put("/agent/v1/reminders", json={"hour": 24})).status_code == 422


def test_agent_interface_refuses_to_start_without_a_token():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            coach_interface="agent",
            agent_api_token="",
            _env_file=None,
            telegram_bot_token="x",
            intervals_api_key="x",
        )
