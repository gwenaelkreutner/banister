"""Tests for the evening calorie-tracking reminder's selection logic (spec 008 US5).

Only the pure predicate is tested here — the sleep loop itself (`app/main.py`) is
validated live, matching this codebase's existing practice for the session reminder
(confirmed by grep before writing this: no test exercises `_run_session_reminders` either).
"""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import meal_entry_repo
from app.services.nutrition_reminder import needs_reminder


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_needs_reminder_true_with_nothing_logged_today(db_session):
    user = await _make_user(db_session, 5001)
    assert await needs_reminder(db_session, user.id, date.today()) is True


async def test_needs_reminder_false_after_any_entry_type(db_session):
    user = await _make_user(db_session, 5002)
    await meal_entry_repo.create(
        db_session,
        user_id=user.id,
        entry_date=date.today(),
        entry_type="day_recap",
        meal_slot=None,
        raw_description="récap rapide",
        estimated_calories=1800,
    )
    await db_session.commit()

    assert await needs_reminder(db_session, user.id, date.today()) is False


async def test_needs_reminder_only_looks_at_today(db_session):
    """An entry logged for a different day (e.g. a backdated 'hier soir') must not
    excuse today's reminder."""
    from datetime import timedelta

    user = await _make_user(db_session, 5003)
    await meal_entry_repo.create(
        db_session,
        user_id=user.id,
        entry_date=date.today() - timedelta(days=1),
        entry_type="meal",
        meal_slot="dinner",
        raw_description="hier soir",
        estimated_calories=700,
    )
    await db_session.commit()

    assert await needs_reminder(db_session, user.id, date.today()) is True
