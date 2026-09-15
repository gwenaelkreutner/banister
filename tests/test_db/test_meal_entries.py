"""Repository-level tests for daily calorie tracking (spec 008).

Covers the deterministic surface of the feature — persistence and aggregation — across
Foundational (create/daily_totals/cascade), US2 (day-recap replace semantics), US3
(multi-day history range), and US4 (undo: latest-entry lookup + delete). Tool-level
dispatch and validation live in tests/test_llm/test_nutrition_tools.py.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select

from app.db.models.meal_entry import MealEntry
from app.db.models.user import User
from app.db.repositories import meal_entry_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


# ── Foundational: create / daily_totals / cascade ────────────────────────────


async def test_create_persists_every_field(db_session):
    user = await _make_user(db_session, 3001)
    entry = await meal_entry_repo.create(
        db_session,
        user_id=user.id,
        entry_date=date(2026, 9, 15),
        entry_type="meal",
        meal_slot="lunch",
        raw_description="3 œufs, du pain, une pomme",
        estimated_calories=650,
    )
    await db_session.commit()

    fetched = (
        await db_session.execute(select(MealEntry).where(MealEntry.id == entry.id))
    ).scalar_one()
    assert fetched.user_id == user.id
    assert fetched.entry_date == date(2026, 9, 15)
    assert fetched.entry_type == "meal"
    assert fetched.meal_slot == "lunch"
    assert fetched.raw_description == "3 œufs, du pain, une pomme"
    assert fetched.estimated_calories == 650


async def test_daily_totals_sums_same_day_entries_and_omits_empty_days(db_session):
    user = await _make_user(db_session, 3002)
    d = date(2026, 9, 15)
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="lunch", raw_description="a", estimated_calories=600,
    )
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="dinner", raw_description="b", estimated_calories=800,
    )
    await db_session.commit()

    totals = await meal_entry_repo.daily_totals(db_session, user.id, d, d)
    assert len(totals) == 1
    assert totals[0].entry_date == d
    assert totals[0].total_calories == 1400
    assert totals[0].entry_count == 2

    # A day with zero entries is absent from the result, never present at 0 (FR-008).
    empty_day = date(2026, 9, 16)
    totals_empty = await meal_entry_repo.daily_totals(db_session, user.id, empty_day, empty_day)
    assert totals_empty == []


async def test_cascade_delete_removes_meal_entries(db_session):
    """Mirrors test_cascade.py's pattern: delete via a Core statement so the DB-level
    ondelete='CASCADE' is what's proven, independent of ORM relationship bookkeeping."""
    user = await _make_user(db_session, 3003)
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=date(2026, 9, 15), entry_type="meal",
        meal_slot=None, raw_description="a", estimated_calories=500,
    )
    await db_session.commit()

    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()

    remaining = (
        await db_session.execute(select(MealEntry).where(MealEntry.user_id == user.id))
    ).scalars().all()
    assert remaining == []


# ── US2: day-recap replace semantics ──────────────────────────────────────────


async def test_delete_for_date_replace(db_session):
    user = await _make_user(db_session, 3004)
    d = date(2026, 9, 15)
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="lunch", raw_description="a", estimated_calories=600,
    )
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="dinner", raw_description="b", estimated_calories=700,
    )
    await db_session.commit()

    deleted_count = await meal_entry_repo.delete_for_date(db_session, user.id, d)
    await db_session.commit()

    assert deleted_count == 2
    remaining = await meal_entry_repo.daily_totals(db_session, user.id, d, d)
    assert remaining == []

    # A day with nothing to delete reports zero, not an error.
    assert await meal_entry_repo.delete_for_date(db_session, user.id, d) == 0


# ── US3: multi-day history range ──────────────────────────────────────────────


async def test_daily_totals_over_range(db_session):
    user = await _make_user(db_session, 3005)
    d1, d2, d3 = date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)
    # d2 is deliberately left empty — the gap the history tool must represent as
    # "not logged," not as a logged zero.
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d1, entry_type="day_recap",
        meal_slot=None, raw_description="jour 1", estimated_calories=2100,
    )
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d3, entry_type="meal",
        meal_slot="lunch", raw_description="jour 3a", estimated_calories=900,
    )
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d3, entry_type="meal",
        meal_slot="dinner", raw_description="jour 3b", estimated_calories=1050,
    )
    await db_session.commit()

    totals = await meal_entry_repo.daily_totals(db_session, user.id, d1, d3)
    by_date = {t.entry_date: t for t in totals}

    assert len(totals) == 2  # d2 absent
    assert by_date[d1].total_calories == 2100
    assert by_date[d1].entry_count == 1
    assert d2 not in by_date
    assert by_date[d3].total_calories == 1950
    assert by_date[d3].entry_count == 2


# ── US4: undo (latest-entry lookup + delete) ──────────────────────────────────


async def test_get_latest_for_date_and_delete_undo(db_session):
    user = await _make_user(db_session, 3006)
    d = date(2026, 9, 15)
    await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="lunch", raw_description="premier", estimated_calories=500,
    )
    await db_session.commit()
    second = await meal_entry_repo.create(
        db_session, user_id=user.id, entry_date=d, entry_type="meal",
        meal_slot="dinner", raw_description="deuxieme (erreur)", estimated_calories=900,
    )
    await db_session.commit()

    latest = await meal_entry_repo.get_latest_for_date(db_session, user.id, d)
    assert latest is not None
    assert latest.id == second.id  # the most recently created, not the highest calorie

    await meal_entry_repo.delete(db_session, latest)
    await db_session.commit()

    totals = await meal_entry_repo.daily_totals(db_session, user.id, d, d)
    assert len(totals) == 1
    assert totals[0].total_calories == 500

    # Nothing left today after the last entry is also undone.
    only_left = await meal_entry_repo.get_latest_for_date(db_session, user.id, d)
    await meal_entry_repo.delete(db_session, only_left)
    await db_session.commit()
    assert await meal_entry_repo.get_latest_for_date(db_session, user.id, d) is None
