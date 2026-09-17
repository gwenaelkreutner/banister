"""Changing the goal does not erase the athlete (spec 007 US3, FR-011..FR-016, SC-005).

`parse_goal_date` is pure. `_regenerate` needs a populated DB — a user with an active
plan, a profile, logged sessions, adherence, chat.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.bot.routers.goal import _enter_freestyle_mode, _regenerate, cmd_goal, goal_type
from app.bot.routers.setup import parse_goal_date
from app.db.models.chat_message import ChatMessage
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.db.models.weekly_adherence import WeeklyAdherence
from app.db.repositories import plan_repo, profile_repo, session_log_repo
from app.engine.plan_builder import generate_plan
from tests.test_engine.test_plan_builder import make_profile

# ── parse_goal_date (FR-015) ────────────────────────────────────────────────


def test_parse_goal_date_rejects_the_past_and_garbage():
    assert parse_goal_date("nope")[1] == "format"
    assert parse_goal_date("2000-01-01")[1] == "past"
    assert parse_goal_date("aucune") == (None, None)


def test_parse_goal_date_flags_too_soon_and_too_far_but_keeps_the_date():
    soon = (date.today() + timedelta(days=10)).isoformat()
    d, problem = parse_goal_date(soon)
    assert problem == "too_soon" and d is not None

    far = (date.today() + timedelta(days=500)).isoformat()
    d, problem = parse_goal_date(far)
    assert problem == "too_far" and d is not None

    ok = (date.today() + timedelta(days=90)).isoformat()
    assert parse_goal_date(ok)[1] is None


# ── _regenerate (FR-011, FR-012, SC-005) ────────────────────────────────────


class _Msg:
    def __init__(self):
        self.sent = []
        self.markups = []

    async def answer(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))

    async def edit_text(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))


class _Callback:
    def __init__(self, data: str):
        self.data = data
        self.message = _Msg()
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


class _State:
    def __init__(self):
        self._d = {}
        self.state = None

    async def get_data(self):
        return dict(self._d)

    async def update_data(self, **kw):
        self._d.update(kw)

    async def set_state(self, s):
        self.state = s

    async def clear(self):
        self._d.clear()
        self.state = None  # matches real aiogram FSMContext.clear() semantics


async def _populate(db_session) -> User:
    u = User(telegram_id=1, first_name="Gwen")
    db_session.add(u)
    await db_session.flush()

    schema = generate_plan(make_profile(goal_type="event"))
    plan = await plan_repo.create(
        db_session, u.id, plan_technical=schema.model_dump(mode="json"),
        start_date=schema.weeks[0].start_date, end_date=schema.weeks[0].start_date,
    )
    prof = make_profile(goal_type="event").model_dump(mode="json")
    await profile_repo.create(db_session, u.id, prof)

    for i in range(6):
        db_session.add(SessionLog(
            user_id=u.id, plan_id=plan.id, week_number=1, day_of_week=i % 7,
            logged_date=date.today() - timedelta(days=i), status="done", tss_actual=60.0,
        ))
    for i in range(3):
        db_session.add(WeeklyAdherence(
            user_id=u.id, week_start_date=date.today() - timedelta(days=7 * (i + 1)),
            sessions_done=3, tss_7d=180.0,
        ))
    for i in range(4):
        db_session.add(ChatMessage(user_id=u.id, role="user", content=f"m{i}"))
    await db_session.flush()
    return u


async def test_goal_change_preserves_all_history(db_session):
    u = await _populate(db_session)
    before = {
        "logs": len(await session_log_repo.get_all_for_user(db_session, u.id)),
        "adh": await db_session.scalar(
            select(func.count()).select_from(WeeklyAdherence)
        ),
        "chat": await db_session.scalar(
            select(func.count()).select_from(ChatMessage)
        ),
    }

    msg, state = _Msg(), _State()
    new_date = date.today() + timedelta(days=120)
    await _regenerate(msg, state, db_session, u, "fitness", new_date)

    after = {
        "logs": len(await session_log_repo.get_all_for_user(db_session, u.id)),
        "adh": await db_session.scalar(
            select(func.count()).select_from(WeeklyAdherence)
        ),
        "chat": await db_session.scalar(
            select(func.count()).select_from(ChatMessage)
        ),
    }
    assert before == after  # SC-005 — 100% preserved

    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
    profile = await profile_repo.get_by_user_id(db_session, u.id)
    assert profile.profile["objective"]["type"] == "fitness"  # goal changed in place
    assert "gardé" in msg.sent[-1] and "change" in msg.sent[-1]  # FR-016 summary


async def test_goal_change_surfaces_stale_calendar_entries(db_session):
    """FR-014 — sessions published under the old plan are flagged, not left silently stale."""
    import uuid
    from datetime import UTC, datetime

    from app.db.models.publication import PublicationApproval, PublishedEntry

    u = await _populate(db_session)
    old_plan = await plan_repo.get_active_plan(db_session, u.id)
    appr = PublicationApproval(
        id=uuid.uuid4(), user_id=u.id, plan_id=old_plan.id, content_hash="x",
        horizon_start=date.today(), horizon_end=date.today() + timedelta(days=13),
        session_count=1, status="approved", requested_at=datetime.now(UTC),
    )
    db_session.add(appr)
    await db_session.flush()
    db_session.add(PublishedEntry(
        id=uuid.uuid4(), user_id=u.id, plan_id=old_plan.id, approval_id=appr.id,
        external_id="banister:x:2026-09-10:endurance-1-2", intervals_event_id="e1",
        session_date=date.today() + timedelta(days=3), week_number=1, day_of_week=2,
        content_hash="h", published_at=datetime.now(UTC),
    ))
    await db_session.flush()

    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))
    assert "calendrier" in msg.sent[-1] and "/publish" in msg.sent[-1]


# ── Mode switch (spec 009 US2) ───────────────────────────────────────────────


async def _make_user_no_plan(db_session, telegram_id: int = 42) -> User:
    from datetime import UTC
    from datetime import datetime as _datetime

    u = User(
        telegram_id=telegram_id, first_name="Gwen",
        onboarding_completed_at=_datetime.now(UTC),
    )
    db_session.add(u)
    await db_session.flush()
    await profile_repo.create(
        db_session, u.id, make_profile(goal_type="event").model_dump(mode="json")
    )
    return u


async def test_goal_no_longer_blocks_without_an_active_plan(db_session):
    """spec 009 research Decision 2 — /goal is also the entry point from freestyle mode."""
    u = await _make_user_no_plan(db_session)
    msg, state = _Msg(), _State()

    await cmd_goal(msg, state, db_session, u)

    assert "Aucun plan actif" not in msg.sent[-1]
    assert state.state is not None
    assert msg.markups[-1] is not None  # the (now five-option) keyboard was shown


async def test_goal_freestyle_option_deactivates_plan_and_withdraws_calendar(
    db_session, monkeypatch
):
    u = await _populate(db_session)
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None

    calls = []

    async def _fake_withdraw(session, client, user, plan_):
        calls.append(plan_.id)
        return (2, 0)

    monkeypatch.setattr(
        "app.services.publication.withdraw_all_publications", _fake_withdraw
    )

    callback, state = _Callback("goal:type:freestyle"), _State()
    await _enter_freestyle_mode(callback, state, db_session, u)

    assert await plan_repo.get_active_plan(db_session, u.id) is None
    assert calls == [plan.id]
    assert "Mode libre" in callback.message.sent[-1]
    assert "2 séance" in callback.message.sent[-1]
    assert callback.answered == 1

    # Nothing else was touched (FR-012).
    logs_after = await session_log_repo.get_all_for_user(db_session, u.id)
    assert len(logs_after) == 6


async def test_goal_freestyle_option_is_idempotent_when_already_freestyle(db_session):
    u = await _make_user_no_plan(db_session)
    callback, state = _Callback("goal:type:freestyle"), _State()

    await _enter_freestyle_mode(callback, state, db_session, u)

    assert "Déjà en mode libre" in callback.message.sent[-1]
    assert callback.answered == 1


async def test_goal_type_dispatches_freestyle_without_asking_for_a_date(db_session):
    u = await _make_user_no_plan(db_session)
    callback, state = _Callback("goal:type:freestyle"), _State()
    await state.set_state("GoalStates.GOAL")  # arbitrary — goal_type doesn't branch on it

    await goal_type(callback, state, db_session, u)

    assert state.state is None  # cleared, never advanced to GoalStates.DATE
    assert "libre" in callback.message.sent[-1].lower()


async def test_regenerate_from_freestyle_omits_the_old_plan_diff(db_session):
    """spec 009 — entering goal mode from freestyle has no old plan to diff against;
    'ce qui est gardé' still appears, 'ce qui change' does not."""
    u = await _make_user_no_plan(db_session)
    assert await plan_repo.get_active_plan(db_session, u.id) is None

    msg, state = _Msg(), _State()
    await _regenerate(msg, state, db_session, u, "fitness", date.today() + timedelta(days=120))

    summary = msg.sent[-1]
    assert "gardé" in summary
    assert "change" not in summary
    plan = await plan_repo.get_active_plan(db_session, u.id)
    assert plan is not None
