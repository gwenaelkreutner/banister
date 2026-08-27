"""Poller detection tests (spec 002 T042).

Nothing here exercises actual notification delivery — Phase 5's poller only detects and
bounds; Phase 6 wires delivery. Covers: exactly-once detection (FR-009), an edited
activity is not re-detected (FR-010), the interval floor (FR-007/FR-008), announcement
bounding (FR-011), overlapping-call protection (FR-014), and bounded retry (FR-013).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models.user import User
from app.db.repositories import sync_state_repo
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
