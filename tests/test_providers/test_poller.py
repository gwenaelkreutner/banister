"""Poller detection tests (spec 002 T042).

Nothing here exercises actual notification delivery — Phase 5's poller only detects and
bounds; Phase 6 wires delivery. Covers: exactly-once detection (FR-009), an edited
activity is not re-detected (FR-010), the interval floor (FR-007/FR-008), announcement
bounding (FR-011), overlapping-call protection (FR-014), and bounded retry (FR-013).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.base import Base
from app.db.models.user import User
from app.db.repositories import activity_repo, sync_state_repo, wellness_repo
from app.providers.intervals import poller
from app.providers.intervals.errors import (
    CredentialRejectedError,
    RateLimitedError,
    TransientError,
)


class _FakeClient:
    def __init__(self, activities: list[dict], *, raise_first: Exception | None = None):
        self._activities = activities
        self._raise_first = raise_first
        self.calls = 0

    async def list_activities(self, *, oldest: str, newest: str) -> list[dict]:
        self.calls += 1
        if self._raise_first is not None and self.calls == 1:
            exc = self._raise_first
            self._raise_first = None
            raise exc
        return self._activities


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


class TestDetectNewActivities:
    @pytest.mark.parametrize("month", [1, 9])
    async def test_detection_uses_paris_day_when_utc_differs(self, db_session, monkeypatch, month):
        from app.core import time

        instant = datetime(2026, month, 28, 23, 30, tzinfo=UTC)

        class ParisClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz)

        monkeypatch.setattr(time, "datetime", ParisClock)
        user = await _make_user(db_session, 621)

        class Client:
            async def list_activities(self, *, oldest, newest):
                assert newest == date(2026, month, 29).isoformat()
                assert oldest == date(2026, month, 22).isoformat()
                return []

        assert await poller.detect_new_activities(db_session, user.id, Client()) == []

    async def test_returns_only_unreported_activities(self, db_session):
        user = await _make_user(db_session, 600)
        await sync_state_repo.mark_reported(
            db_session, user.id, "already-reported", reported_at=datetime.now(UTC)
        )
        await db_session.commit()

        client = _FakeClient(
            [
                {"id": "already-reported", "start_date": "2026-08-25T10:00:00Z"},
                {"id": "brand-new", "start_date": "2026-08-26T10:00:00Z"},
            ]
        )

        result = await poller.detect_new_activities(db_session, user.id, client)

        assert [a["id"] for a in result] == ["brand-new"]

    async def test_edited_activity_keeps_its_id_and_is_not_redetected(self, db_session):
        """FR-010: an edit/rename at the source does not change the activity id, so it
        must stay filtered out exactly like any other already-reported activity."""
        user = await _make_user(db_session, 601)
        await sync_state_repo.mark_reported(
            db_session, user.id, "activity-42", reported_at=datetime.now(UTC)
        )
        await db_session.commit()

        # Same id, different name/content — simulates the source's own edit
        client = _FakeClient(
            [{"id": "activity-42", "start_date": "2026-08-25T10:00:00Z", "name": "Renamed ride"}]
        )

        result = await poller.detect_new_activities(db_session, user.id, client)

        assert result == []

    async def test_results_sorted_oldest_first(self, db_session):
        user = await _make_user(db_session, 602)
        client = _FakeClient(
            [
                {"id": "b", "start_date": "2026-08-26T10:00:00Z"},
                {"id": "a", "start_date": "2026-08-24T10:00:00Z"},
                {"id": "c", "start_date": "2026-08-27T10:00:00Z"},
            ]
        )

        result = await poller.detect_new_activities(db_session, user.id, client)

        assert [a["id"] for a in result] == ["a", "b", "c"]


class TestBoundAnnouncements:
    def test_returns_everything_unbounded_when_under_the_limit(self):
        activities = [{"id": str(i)} for i in range(2)]
        to_announce, ingest_only = poller.bound_announcements(activities, max_announcements=3)
        assert to_announce == activities
        assert ingest_only == []

    def test_splits_oldest_first_when_over_the_limit(self):
        activities = [{"id": str(i)} for i in range(5)]
        to_announce, ingest_only = poller.bound_announcements(activities, max_announcements=3)
        assert [a["id"] for a in to_announce] == ["0", "1", "2"]
        assert [a["id"] for a in ingest_only] == ["3", "4"]
        # every activity is accounted for, none dropped
        assert len(to_announce) + len(ingest_only) == 5


class TestPollIntervalFloor:
    def test_configured_value_at_or_above_the_floor_is_used_as_is(self, monkeypatch):
        monkeypatch.setattr(poller.settings, "intervals_poll_interval_minutes", 5)
        assert poller.resolve_poll_interval_minutes() == 5

    def test_configured_value_below_the_floor_is_clamped(self, monkeypatch):
        monkeypatch.setattr(poller.settings, "intervals_poll_interval_minutes", 0)
        assert poller.resolve_poll_interval_minutes() == poller.MIN_POLL_INTERVAL_MINUTES


class TestPollOnceRetryAndLocking:
    async def test_skips_when_a_previous_poll_is_still_in_flight(self, db_session, monkeypatch):
        user = await _make_user(db_session, 603)
        client = _FakeClient([])

        # Simulate an in-flight poll by holding the lock ourselves.
        await poller._poll_lock.acquire()
        try:
            result = await poller.poll_once(db_session, user.id, client)
        finally:
            poller._poll_lock.release()

        assert result == []
        assert client.calls == 0  # never even attempted — skipped before calling out

    async def test_retries_a_transient_error_then_succeeds(self, db_session, monkeypatch):
        monkeypatch.setattr(poller, "_RETRY_DELAYS_S", (0, 0))
        user = await _make_user(db_session, 604)
        client = _FakeClient(
            [{"id": "recovered", "start_date": "2026-08-26T10:00:00Z"}],
            raise_first=TransientError("temporary"),
        )

        result = await poller.poll_once(db_session, user.id, client)

        assert [a["id"] for a in result] == ["recovered"]
        assert client.calls == 2  # one failure, one success

    async def test_rate_limited_waits_and_retries(self, db_session, monkeypatch):
        monkeypatch.setattr(poller, "_RETRY_DELAYS_S", (0, 0))
        user = await _make_user(db_session, 605)
        client = _FakeClient(
            [{"id": "recovered", "start_date": "2026-08-26T10:00:00Z"}],
            raise_first=RateLimitedError("slow down", retry_after_seconds=0.001),
        )

        result = await poller.poll_once(db_session, user.id, client)

        assert [a["id"] for a in result] == ["recovered"]

    async def test_credential_rejected_is_not_retried(self, db_session, monkeypatch):
        monkeypatch.setattr(poller, "_RETRY_DELAYS_S", (0, 0))
        user = await _make_user(db_session, 606)
        client = _FakeClient([], raise_first=CredentialRejectedError("bad key"))

        with pytest.raises(CredentialRejectedError):
            await poller.poll_once(db_session, user.id, client)

        assert client.calls == 1  # no retry attempted

    async def test_exhausting_all_retries_returns_empty_rather_than_raising(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(poller, "_RETRY_DELAYS_S", (0, 0))
        user = await _make_user(db_session, 607)

        class _AlwaysFails(_FakeClient):
            async def list_activities(self, *, oldest: str, newest: str) -> list[dict]:
                self.calls += 1
                raise TransientError("still down")

        client = _AlwaysFails([])

        result = await poller.poll_once(db_session, user.id, client)

        assert result == []
        assert client.calls == len(poller._RETRY_DELAYS_S) + 1


@pytest.mark.parametrize("initial_import", [True, False])
@pytest.mark.parametrize("outcome", ["rest", "detection_error", "notification_error", "no_bot"])
async def test_scheduler_persists_source_data_before_detection_and_delivery(
    tmp_path, monkeypatch, initial_import, outcome
):
    """Read after scheduler session closure: a flush alone cannot satisfy this test."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'poller.sqlite'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = date(2026, 9, 29)
    monkeypatch.setattr(poller, "paris_today", lambda: today)

    class Client:
        wellness_windows = []

        async def list_activities(self, *, oldest, newest):
            if (date.fromisoformat(newest) - date.fromisoformat(oldest)).days > 7:
                return [{"id": "historical", "start_date_local": "2026-09-01T10:00:00"}]
            if outcome == "detection_error":
                raise CredentialRejectedError("bad key")
            return [] if outcome == "rest" else [{"id": "new"}]

        async def list_wellness(self, *, oldest, newest):
            self.wellness_windows.append((oldest, newest))
            return [{
                "id": str(today), "ctl": 65, "atl": 50, "weight": 72.5,
                "hrv": 61, "restingHR": 48, "sleepSecs": 27000,
                "sleepScore": 83, "spO2": 97, "menstrualPhase": "LUTEAL",
            }]

    client = Client()
    sleeps = 0

    async def one_tick(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    notifications = []

    async def failed_notification(*args, **kwargs):
        notifications.append(kwargs)
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(poller.asyncio, "sleep", one_tick)
    monkeypatch.setattr(
        "app.providers.intervals.notifier.notify_detected_activity", failed_notification
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            user = await _make_user(session, 620)
            user_id = user.id
            if not initial_import:
                await sync_state_repo.mark_history_import_complete(session, user_id)
            await wellness_repo.upsert(session, user_id, today, ctl=30, atl=40, weight_kg=75)
            await session.commit()

        with pytest.raises(asyncio.CancelledError):
            await poller.run_poller_scheduler(
                factory, lambda: client, bot=None if outcome == "no_bot" else object()
            )

        async with factory() as reader:
            row = await wellness_repo.get_by_date(reader, user_id, today)
            assert (row.ctl, row.atl, row.weight_kg) == (65, 50, 72.5)
            assert (row.hrv, row.resting_hr, row.sleep_seconds) == (61, 48, 27000)
            assert (row.sleep_score, row.spo2, row.menstrual_phase) == (83, 97, "LUTEAL")
            state = await sync_state_repo.get_or_create_sync_state(reader, user_id)
            assert state.history_import_complete is True
            if initial_import:
                activities = await activity_repo.get_in_range(
                    reader, user_id, date(2026, 9, 1), today
                )
                assert [a.source_activity_id for a in activities] == ["historical"]
            assert not await sync_state_repo.is_reported(reader, user_id, "new")
        assert client.wellness_windows[-1] == ("2026-09-24", "2026-09-29")
        assert bool(notifications) == (outcome == "notification_error")
    finally:
        await engine.dispose()
