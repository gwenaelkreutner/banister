"""get_recent_for_user() — alimente le picker /review (5 dernières séances). Runs against
SQLite always and PostgreSQL when reachable (tests/test_db/conftest.py's dual-backend
db_session fixture), same convention as test_session_log_freestyle.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.models.user import User
from app.db.repositories import session_log_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def _log(session, user_id, days_ago: int, status: str = "done"):
    return await session_log_repo.create(
        session,
        user_id=user_id,
        plan_id=None,
        week_number=None,
        day_of_week=None,
        logged_date=date.today() - timedelta(days=days_ago),
        status=status,
        tss_actual=50.0,
    )


async def test_returns_at_most_limit_most_recent_first(db_session):
    user = await _make_user(db_session, 8001)
    for days_ago in [10, 1, 5, 3, 8, 2, 7]:  # 7 logs, limit défaut = 5
        await _log(db_session, user.id, days_ago)
    await db_session.flush()

    recent = await session_log_repo.get_recent_for_user(db_session, user.id)

    assert len(recent) == 5
    ordered_days_ago = [(date.today() - log.logged_date).days for log in recent]
    assert ordered_days_ago == sorted(ordered_days_ago)  # plus récent (petit) en premier
    assert ordered_days_ago[0] == 1


async def test_excludes_skipped_sessions(db_session):
    user = await _make_user(db_session, 8002)
    await _log(db_session, user.id, days_ago=1, status="skipped")
    await _log(db_session, user.id, days_ago=2, status="done")
    await db_session.flush()

    recent = await session_log_repo.get_recent_for_user(db_session, user.id)

    assert len(recent) == 1
    assert recent[0].status == "done"


async def test_includes_unplanned_freestyle_logs(db_session):
    user = await _make_user(db_session, 8003)
    await _log(db_session, user.id, days_ago=1, status="unplanned")
    await db_session.flush()

    recent = await session_log_repo.get_recent_for_user(db_session, user.id)

    assert len(recent) == 1


async def test_isolated_per_user(db_session):
    user_a = await _make_user(db_session, 8004)
    user_b = await _make_user(db_session, 8005)
    await _log(db_session, user_a.id, days_ago=1)
    await _log(db_session, user_b.id, days_ago=1)
    await db_session.flush()

    recent_a = await session_log_repo.get_recent_for_user(db_session, user_a.id)

    assert len(recent_a) == 1
    assert recent_a[0].user_id == user_a.id


async def test_respects_custom_limit(db_session):
    user = await _make_user(db_session, 8006)
    for days_ago in range(3):
        await _log(db_session, user.id, days_ago)
    await db_session.flush()

    recent = await session_log_repo.get_recent_for_user(db_session, user.id, limit=2)

    assert len(recent) == 2
