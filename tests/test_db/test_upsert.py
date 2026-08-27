"""Dialect-neutral upsert tests (spec 003 research R4, contracts/persistence.md guarantee).

Each repository's upsert function is called twice with the same key. The contract is:
one row, carrying the second call's values — never two rows.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.models.activity import Activity
from app.db.models.user import User
from app.db.repositories import activity_repo, weekly_adherence_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_activity_bulk_insert_ignores_duplicate_on_second_call(db_session):
    user = await _make_user(db_session, 100)
    row = {
        "source": "intervals_icu",
        "source_activity_id": "999",
        "activity_date": date(2026, 8, 20),
        "tss": 50.0,
    }

    inserted_first = await activity_repo.bulk_insert(db_session, user.id, [row])
    await db_session.commit()

    inserted_second = await activity_repo.bulk_insert(
        db_session, user.id, [{**row, "tss": 999.0}]
    )
    await db_session.commit()

    assert inserted_first == 1
    assert inserted_second == 0  # conflict target hit, silently skipped

    result = await db_session.execute(
        select(Activity).where(
            Activity.user_id == user.id, Activity.source_activity_id == "999"
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].tss == 50.0  # first call's value, second was a no-op — not an overwrite


async def test_activity_bulk_insert_allows_multiple_null_source_activity_id(db_session):
    """The unique index is partial (WHERE source_activity_id IS NOT NULL) precisely because
    manually logged activities have no source id and must not collide with each other."""
    user = await _make_user(db_session, 101)
    rows = [
        {"source": "manual", "source_activity_id": None, "activity_date": date(2026, 8, 20)},
        {"source": "manual", "source_activity_id": None, "activity_date": date(2026, 8, 21)},
    ]
    inserted = await activity_repo.bulk_insert(db_session, user.id, rows)
    await db_session.commit()
    assert inserted == 2


async def test_weekly_adherence_upsert_updates_rather_than_duplicates(db_session):
    user = await _make_user(db_session, 103)
    week = date(2026, 8, 24)

    await weekly_adherence_repo.upsert(
        db_session, user.id, week, sessions_done=3, tss_7d=200.0
    )
    await db_session.commit()

    await weekly_adherence_repo.upsert(
        db_session, user.id, week, sessions_done=5, tss_7d=350.0
    )
    await db_session.commit()

    rows = await weekly_adherence_repo.get_recent(db_session, user.id, limit=10)
    assert len(rows) == 1
    assert rows[0].sessions_done == 5
    assert rows[0].tss_7d == 350.0
