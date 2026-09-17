"""Coaching mode is derived from plan existence, never stored (spec 009 research
Decision 1) — these tests pin that contract directly against the DB, no mocking."""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import plan_repo
from app.services.coaching_mode import get_coaching_mode, mode_from_plan


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _plan_technical() -> dict:
    return {
        "weeks": [],
        "zones": {},
        "initial_weekly_tss": 100,
        "peak_weekly_tss": 200,
        "weeks_count": 1,
        "coaching_mode": "power",
    }


class TestGetCoachingMode:
    async def test_no_active_plan_is_freestyle(self, db_session):
        user = await _make_user(db_session, 900)

        mode = await get_coaching_mode(db_session, user.id)

        assert mode == "freestyle"

    async def test_active_plan_is_goal(self, db_session):
        user = await _make_user(db_session, 901)
        await plan_repo.create(
            db_session,
            user.id,
            plan_technical=_plan_technical(),
            start_date=date(2026, 1, 5),
            end_date=date(2026, 3, 5),
        )

        mode = await get_coaching_mode(db_session, user.id)

        assert mode == "goal"

    async def test_flips_to_freestyle_immediately_after_deactivation(self, db_session):
        user = await _make_user(db_session, 902)
        await plan_repo.create(
            db_session,
            user.id,
            plan_technical=_plan_technical(),
            start_date=date(2026, 1, 5),
            end_date=date(2026, 3, 5),
        )
        assert await get_coaching_mode(db_session, user.id) == "goal"

        await plan_repo.deactivate_all_for_user(db_session, user.id)

        assert await get_coaching_mode(db_session, user.id) == "freestyle"


class TestModeFromPlan:
    def test_none_plan_is_freestyle(self):
        assert mode_from_plan(None) == "freestyle"

    def test_any_truthy_plan_is_goal(self):
        assert mode_from_plan(object()) == "goal"
