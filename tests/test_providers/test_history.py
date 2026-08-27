"""First-connection history import tests (spec 002 T038-T041).

Not explicitly named as a task in tasks.md (only the poller got a dedicated test task,
T042) — added anyway, matching the rest of this feature's practice of covering new
provider logic (Principle V) rather than leaving FR-020/FR-027/FR-029's guarantees
unverified just because no task line named the file.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models.activity import Activity
from app.db.models.user import User
from app.db.repositories import sync_state_repo
from app.providers.intervals.history import TARGET_HISTORY_DAYS, import_history


class _FakeClient:
    def __init__(self, activities: list[dict]):
        self._activities = activities
        self.calls = 0

    async def list_activities(self, *, oldest: str, newest: str) -> list[dict]:
        self.calls += 1
        return self._activities


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _activity(activity_id: str, days_ago: int, *, tss=50.0) -> dict:
    d = date.today().replace(day=1)  # stable-ish; exact date doesn't matter for these tests
    return {
        "id": activity_id,
        "type": "Ride",
        "start_date": f"{d.isoformat()}T10:00:00Z",
        "start_date_local": f"{d.isoformat()}T10:00:00",
        "elapsed_time": 3600,
        "moving_time": 3500,
        "distance": 30000.0,
        "total_elevation_gain": 200.0,
        "icu_average_watts": 180,
        "icu_weighted_avg_watts": 200,
        "device_watts": True,
        "average_heartrate": 140,
        "max_heartrate": 170,
        "icu_joules": 630000,
        "icu_training_load": tss,
    }


class TestImportHistory:
    async def test_populates_activities_from_the_payload(self, db_session):
        user = await _make_user(db_session, 700)
        client = _FakeClient([_activity("a1", 10), _activity("a2", 20)])

        result = await import_history(db_session, user.id, client)
        await db_session.commit()

        assert result.activity_count == 2
        assert result.inserted_count == 2
        assert result.complete is True

        rows = (
            await db_session.execute(select(Activity).where(Activity.user_id == user.id))
        ).scalars().all()
        assert {r.source_activity_id for r in rows} == {"a1", "a2"}
        assert all(r.source == "intervals_icu" for r in rows)

    async def test_second_call_is_a_noop_once_complete(self, db_session):
        user = await _make_user(db_session, 701)
        client = _FakeClient([_activity("a1", 10)])

        await import_history(db_session, user.id, client)
        await db_session.commit()

        second = await import_history(db_session, user.id, client)

        assert second.activity_count == 0
        assert second.complete is True
        assert client.calls == 1  # never called list_activities again

    async def test_marks_sync_state_complete_with_a_cursor(self, db_session):
        user = await _make_user(db_session, 702)
        client = _FakeClient([_activity("a1", 10)])

        await import_history(db_session, user.id, client, today=date(2026, 8, 27))
        await db_session.commit()

        state = await sync_state_repo.get_or_create_sync_state(db_session, user.id)
        assert state.history_import_complete is True
        assert state.history_import_cursor_date == date(2026, 8, 27) - timedelta(
            days=TARGET_HISTORY_DAYS
        )

    async def test_null_training_load_stays_none_not_zero(self, db_session):
        user = await _make_user(db_session, 703)
        client = _FakeClient([_activity("a1", 10, tss=None)])

        await import_history(db_session, user.id, client)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Activity).where(
                    Activity.user_id == user.id, Activity.source_activity_id == "a1"
                )
            )
        ).scalar_one()
        assert row.tss is None
        assert row.tss_method is None

    async def test_little_history_is_flagged(self, db_session):
        user = await _make_user(db_session, 704)
        client = _FakeClient([_activity("a1", 5)])  # far fewer than 84/7 = 12

        result = await import_history(db_session, user.id, client)

        assert result.little_or_no_history is True

    async def test_plenty_of_history_is_not_flagged(self, db_session):
        user = await _make_user(db_session, 705)
        client = _FakeClient([_activity(f"a{i}", i) for i in range(20)])

        result = await import_history(db_session, user.id, client)

        assert result.little_or_no_history is False

    async def test_rerunning_before_marking_complete_does_not_duplicate(self, db_session):
        """Simulates an interruption: activities got inserted but history_import_complete
        was never set (process died in between). Re-running must not duplicate rows —
        activity_repo.bulk_insert's on_conflict_do_nothing is what actually guarantees
        this; this test proves the guarantee holds end to end through import_history."""
        user = await _make_user(db_session, 706)
        client = _FakeClient([_activity("a1", 10), _activity("a2", 20)])

        await import_history(db_session, user.id, client)
        await db_session.commit()

        # Simulate "interrupted before completion was recorded"
        state = await sync_state_repo.get_or_create_sync_state(db_session, user.id)
        state.history_import_complete = False
        await db_session.flush()
        await db_session.commit()

        await import_history(db_session, user.id, client)
        await db_session.commit()

        count = await db_session.scalar(
            select(func.count()).select_from(Activity).where(Activity.user_id == user.id)
        )
        assert count == 2  # not 4
