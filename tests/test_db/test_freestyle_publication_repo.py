"""spec 010 — freestyle_publication_repo persistence. Runs against SQLite always and
PostgreSQL when reachable (tests/test_db/conftest.py's dual-backend db_session fixture),
same convention as tests/test_db/test_session_log_freestyle.py (spec 009).
"""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import freestyle_publication_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_create_and_fetch_round_trip(db_session):
    user = await _make_user(db_session, 7001)

    entry = await freestyle_publication_repo.create(
        db_session,
        user_id=user.id,
        external_id="banister:freestyle:2026-09-20:endurance-abc12345",
        intervals_event_id="e123",
        session_date=date(2026, 9, 20),
        workout_type="endurance",
        content_hash="deadbeef",
    )

    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert [e.id for e in active] == [entry.id]
    assert active[0].withdrawn_at is None


async def test_get_active_for_user_excludes_withdrawn_rows(db_session):
    user = await _make_user(db_session, 7002)
    entry = await freestyle_publication_repo.create(
        db_session,
        user_id=user.id,
        external_id="banister:freestyle:2026-09-20:recovery-11112222",
        intervals_event_id="e456",
        session_date=date(2026, 9, 20),
        workout_type="recovery",
        content_hash="cafef00d",
    )

    await freestyle_publication_repo.mark_withdrawn(db_session, entry.id)

    active = await freestyle_publication_repo.get_active_for_user(db_session, user.id)
    assert active == []


async def test_mark_withdrawn_sets_timestamp(db_session):
    user = await _make_user(db_session, 7003)
    entry = await freestyle_publication_repo.create(
        db_session,
        user_id=user.id,
        external_id="banister:freestyle:2026-09-21:intervals-33334444",
        intervals_event_id="e789",
        session_date=date(2026, 9, 21),
        workout_type="intervals",
        content_hash="0ff1ce",
    )

    await freestyle_publication_repo.mark_withdrawn(db_session, entry.id)
    await db_session.refresh(entry)

    assert entry.withdrawn_at is not None
