"""spec 009 US3 — `session_logs.plan_id`/`week_number`/`day_of_week` accept NULL, so a
freestyle-mode activity (no plan to belong to) can be logged at all. Runs against
SQLite always and PostgreSQL when reachable (tests/test_db/conftest.py's dual-backend
`db_session` fixture) — the actual constraint that matters is enforced by the engine,
not by the (PostgreSQL-only) Alembic migration-mechanics tests in test_migrations.py.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.db.repositories import session_log_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_session_log_accepts_null_plan_position(db_session):
    user = await _make_user(db_session, 6001)

    log = await session_log_repo.create(
        db_session,
        user_id=user.id,
        plan_id=None,
        week_number=None,
        day_of_week=None,
        logged_date=date.today(),
        status="unplanned",
        tss_actual=55.0,
        source="intervals_icu",
        source_activity_id="i-freestyle-1",
    )
    await db_session.flush()

    assert log.id is not None
    fetched = await session_log_repo.get_by_source_activity(db_session, "i-freestyle-1")
    assert fetched is not None
    assert fetched.plan_id is None
    assert fetched.week_number is None
    assert fetched.day_of_week is None


async def test_deleting_a_plan_never_touches_a_freestyle_log(db_session):
    """A NULL foreign key is not subject to ON DELETE CASCADE — a freestyle log survives
    regardless of what happens to any plan (data-model.md)."""
    from app.db.models.training_plan import TrainingPlan

    user = await _make_user(db_session, 6002)
    plan = TrainingPlan(
        id=uuid.uuid4(), user_id=user.id, plan_technical={}, status="active",
        start_date=date.today(), end_date=date.today(),
    )
    db_session.add(plan)
    await db_session.flush()

    freestyle_log = SessionLog(
        id=uuid.uuid4(), user_id=user.id, plan_id=None, week_number=None,
        day_of_week=None, logged_date=date.today(), status="unplanned",
    )
    db_session.add(freestyle_log)
    await db_session.flush()

    await db_session.delete(plan)
    await db_session.flush()

    from sqlalchemy import select

    result = await db_session.execute(select(SessionLog).where(SessionLog.id == freestyle_log.id))
    assert result.scalar_one_or_none() is not None
