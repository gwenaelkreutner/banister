"""Changing the goal does not erase the athlete (spec 007 US3, FR-011..FR-016, SC-005).

`parse_goal_date` is pure. `_regenerate` needs a populated DB — a user with an active
plan, a profile, logged sessions, adherence, chat.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.bot.routers.goal import _regenerate
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

    async def answer(self, text, **kw):
        self.sent.append(text)


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
