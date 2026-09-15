"""Starting over is possible and deliberate (spec 007 US4, FR-017..FR-020, SC-006/SC-007).
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, timedelta

from sqlalchemy import func, select

from app.bot.routers import reset as reset_router
from app.bot.routers.reset import cmd_reset, reset_confirm
from app.db.models.chat_message import ChatMessage
from app.db.models.meal_entry import MealEntry
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.db.repositories import plan_repo, profile_repo, user_repo
from app.engine.plan_builder import generate_plan
from tests.test_engine.test_plan_builder import make_profile


class _Msg:
    def __init__(self, text: str = ""):
        self.text = text
        self.sent: list[str] = []

    async def answer(self, text, **kw):
        self.sent.append(text)


class _State:
    def __init__(self):
        self.state = None
        self.cleared = False

    async def clear(self):
        self.cleared = True

    async def set_state(self, s):
        self.state = s


async def _populate(db_session) -> User:
    u = User(telegram_id=1, first_name="Gwen")
    db_session.add(u)
    await db_session.flush()
    schema = generate_plan(make_profile())
    plan = await plan_repo.create(
        db_session, u.id, plan_technical=schema.model_dump(mode="json"),
        start_date=schema.weeks[0].start_date, end_date=schema.weeks[0].start_date,
    )
    await profile_repo.create(db_session, u.id, make_profile().model_dump(mode="json"))
    for i in range(5):
        db_session.add(SessionLog(
            user_id=u.id, plan_id=plan.id, week_number=1, day_of_week=i,
            logged_date=date.today() - timedelta(days=i), status="done", tss_actual=50.0,
        ))
    for i in range(3):
        db_session.add(ChatMessage(user_id=u.id, role="user", content=f"m{i}"))
    db_session.add(MealEntry(
        user_id=u.id, entry_date=date.today(), entry_type="meal", meal_slot="lunch",
        raw_description="omelette", estimated_calories=500,
    ))
    await db_session.flush()
    return u


async def test_reset_lists_specific_counts(db_session):
    u = await _populate(db_session)
    msg, state = _Msg(), _State()
    await cmd_reset(msg, state, db_session, u)
    body = msg.sent[-1]
    assert "5 séances" in body and "3 messages" in body
    assert "PAS</b> touché" in body and "intervals.icu" in body  # SC-007 promise
    assert "SUPPRIMER" in body
    assert state.state is not None  # entered CONFIRM


async def test_wrong_word_deletes_nothing(db_session):
    u = await _populate(db_session)
    msg, state = _Msg("supprime tout"), _State()
    await reset_confirm(msg, state, db_session, u)

    assert "Rien n'a été supprimé" in msg.sent[-1]
    assert await db_session.scalar(select(func.count()).select_from(SessionLog)) == 5


async def test_exact_word_deletes_everything_local_and_keeps_identity(db_session):
    u = await _populate(db_session)
    await user_repo.set_coach_voice(db_session, u, "zen")
    await user_repo.ack_disclaimer(db_session, u)
    u.onboarding_completed_at = __import__("datetime").datetime.now(
        __import__("datetime").UTC
    )
    await db_session.flush()

    msg, state = _Msg("SUPPRIMER"), _State()
    await reset_confirm(msg, state, db_session, u)

    assert await db_session.scalar(select(func.count()).select_from(SessionLog)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ChatMessage)) == 0
    assert await plan_repo.get_active_plan(db_session, u.id) is None
    assert await profile_repo.get_by_user_id(db_session, u.id) is None

    # spec 008 research R5 — nutrition history is neither training data nor identity;
    # it survives a /reset by explicit athlete request, unlike everything else here.
    assert await db_session.scalar(select(func.count()).select_from(MealEntry)) == 1

    refreshed = await db_session.get(User, u.id)
    assert refreshed is not None                    # user row survives
    assert refreshed.coach_voice == "zen"           # identity kept (FR-020)
    assert refreshed.disclaimer_acknowledged_at is not None
    assert refreshed.onboarding_completed_at is None  # next /setup is a real first run


def test_meal_entries_not_in_purge_models():
    """spec 008 research R5 — a grep, not a vibe: MealEntry must not be one of /reset's
    purge targets, the same standard test_reset_path_issues_no_outbound_call sets for
    the outbound-call guarantee."""
    assert MealEntry not in user_repo._PURGE_MODELS


def test_reset_path_issues_no_outbound_call():
    """FR-020 / SC-007 — a grep, not a vibe: nothing in the reset module reaches the
    intervals.icu client."""
    tree = ast.parse(inspect.getsource(reset_router))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "intervals" not in (node.module or ""), node.module
        if isinstance(node, ast.Attribute):
            assert node.attr not in (
                "get_athlete", "list_events", "create_event", "update_event",
                "delete_event", "list_activities",
            )
