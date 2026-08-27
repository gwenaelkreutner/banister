"""app/services/fitness.py — current CTL/ATL/TSB consumed from the source (spec 002
FR-016, closing the gap the mapper's own docstring flagged as not-yet-done)."""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import wellness_repo
from app.services.fitness import get_current_fitness


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_returns_none_without_any_wellness_row(db_session):
    user = await _make_user(db_session, 500)
    result = await get_current_fitness(db_session, user.id, today=date(2026, 8, 27))
    assert result is None


async def test_uses_todays_wellness_row_verbatim(db_session):
    user = await _make_user(db_session, 501)
    await wellness_repo.upsert(db_session, user.id, date(2026, 8, 27), ctl=45.885956, atl=64.6617)
    await db_session.commit()

    result = await get_current_fitness(db_session, user.id, today=date(2026, 8, 27))

    assert result is not None
    assert result.metrics.ctl == 45.885956
    assert result.metrics.atl == 64.6617
    assert result.metrics.tsb == round(45.885956 - 64.6617, 1)
    assert result.as_of == date(2026, 8, 27)
    assert result.is_stale is False


async def test_falls_back_to_an_older_row_and_flags_it_stale(db_session):
    user = await _make_user(db_session, 502)
    await wellness_repo.upsert(db_session, user.id, date(2026, 8, 25), ctl=44.0, atl=58.8)
    await db_session.commit()

    result = await get_current_fitness(db_session, user.id, today=date(2026, 8, 27))

    assert result is not None
    assert result.as_of == date(2026, 8, 25)
    assert result.is_stale is True


async def test_ignores_a_future_row(db_session):
    """A wellness row dated after `today` must never be used — that would be presenting
    data from the future as the current figure, not just a staleness question."""
    user = await _make_user(db_session, 503)
    await wellness_repo.upsert(db_session, user.id, date(2026, 8, 30), ctl=50.0, atl=40.0)
    await db_session.commit()

    result = await get_current_fitness(db_session, user.id, today=date(2026, 8, 27))
    assert result is None
