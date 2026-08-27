"""Notifier tests (spec 002 T045-T049): the poller-driven staged notification.

The critical property under test is T049/FR-012: an activity must not be marked
reported until delivery has actually succeeded — a Telegram hiccup must leave it
retryable on the next poll tick, not silently swallowed.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from app.db.models.user import User
from app.db.repositories import plan_repo, sync_state_repo
from app.providers.intervals.notifier import notify_detected_activity, process_detected_activity

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "intervals"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _FakeBot:
    def __init__(self, *, fail_on_message: int | None = None):
        self.sent: list[str] = []
        self.actions: list[str] = []
        self._fail_on_message = fail_on_message
        self._message_count = 0

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)

    async def send_message(self, chat_id, text, **kwargs):
        self._message_count += 1
        if self._fail_on_message == self._message_count:
            raise RuntimeError("simulated Telegram outage")
        self.sent.append(text)


class _FakeClient:
    def __init__(self, detail_payload: dict):
        self._detail = detail_payload
        self.detail_calls = 0

    async def get_activity(self, activity_id: str, *, with_intervals: bool = False) -> dict:
        self.detail_calls += 1
        return self._detail


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _plan_technical(day_of_week: int) -> dict:
    # Matches activity_full.json's real, mapped characteristics closely (long_ride,
    # ~127 min, TSS 108, dominant zone Z1) so the match score clears is_aligned's
    # threshold — these tests are about delivery gating, not matching itself (that's
    # covered separately in tests/test_analysis/test_matching.py).
    return {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": 108,
                "sessions": [
                    {
                        "day_of_week": day_of_week,
                        "workout_type": "long_ride",
                        "zone_code": "Z1",
                        "duration_minutes": 127,
                        "target_time_in_zone_minutes": 60,
                        "tss_target": 108,
                        "description_fr": "Sortie longue",
                    }
                ],
            }
        ],
        "zones": {},
        "initial_weekly_tss": 200,
        "peak_weekly_tss": 350,
        "weeks_count": 1,
        "coaching_mode": "power",
    }


async def _make_plan(session, user_id, *, start: date):
    return await plan_repo.create(
        session,
        user_id,
        plan_technical=_plan_technical(start.weekday()),
        start_date=start,
        end_date=start,
    )


def _summary_and_start_date(payload: dict) -> date:
    start = payload.get("start_date_local") or payload["start_date"]
    return datetime.fromisoformat(start.replace("Z", "+00:00")).date()


class TestProcessDetectedActivity:
    async def test_announce_true_fetches_full_detail(self, db_session):
        user = await _make_user(db_session, 900)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)

        await process_detected_activity(db_session, user, client, payload, announce=True)

        assert client.detail_calls == 1

    async def test_announce_false_does_not_fetch_detail(self, db_session):
        user = await _make_user(db_session, 901)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)

        await process_detected_activity(db_session, user, client, payload, announce=False)

        assert client.detail_calls == 0


class TestNotifyDetectedActivityDeliveryGating:
    async def test_matched_activity_marked_reported_only_after_delivery(self, db_session):
        user = await _make_user(db_session, 902)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)
        bot = _FakeBot()

        await notify_detected_activity(db_session, user, bot, client, payload, announce=True)
        await db_session.commit()

        activity_id = str(payload["id"])
        assert await sync_state_repo.is_reported(db_session, user.id, activity_id)
        # 3-message staged exchange actually sent
        assert len(bot.sent) == 3

    async def test_failed_delivery_leaves_activity_unreported(self, db_session):
        """T049/FR-012: a Telegram failure on any of the three staged messages must not
        mark the activity reported — the next poll tick has to retry it."""
        user = await _make_user(db_session, 903)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)
        bot = _FakeBot(fail_on_message=3)  # fails on Message C, the RPE keyboard

        await notify_detected_activity(db_session, user, bot, client, payload, announce=True)
        await db_session.commit()

        activity_id = str(payload["id"])
        assert not await sync_state_repo.is_reported(db_session, user.id, activity_id)

    async def test_retry_after_failed_delivery_does_not_duplicate_the_log(self, db_session):
        user = await _make_user(db_session, 904)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)

        failing_bot = _FakeBot(fail_on_message=1)
        await notify_detected_activity(
            db_session, user, failing_bot, client, payload, announce=True
        )
        await db_session.commit()

        activity_id = str(payload["id"])
        assert not await sync_state_repo.is_reported(db_session, user.id, activity_id)

        working_bot = _FakeBot()
        await notify_detected_activity(
            db_session, user, working_bot, client, payload, announce=True
        )
        await db_session.commit()

        assert await sync_state_repo.is_reported(db_session, user.id, activity_id)
        assert len(working_bot.sent) == 3

        from sqlalchemy import func, select

        from app.db.models.session_log import SessionLog

        count = await db_session.scalar(
            select(func.count()).select_from(SessionLog).where(
                SessionLog.user_id == user.id, SessionLog.source_activity_id == activity_id
            )
        )
        assert count == 1

    async def test_ingest_only_activity_is_marked_reported_without_any_message(self, db_session):
        """FR-011: an ingest-only (backlog, not announced) activity is still ingested —
        and, since nothing was owed to the athlete for it, immediately reported."""
        user = await _make_user(db_session, 905)
        payload = _load("activity_full.json")
        activity_date = _summary_and_start_date(payload)
        await _make_plan(db_session, user.id, start=activity_date)
        client = _FakeClient(payload)
        bot = _FakeBot()

        await notify_detected_activity(db_session, user, bot, client, payload, announce=False)
        await db_session.commit()

        activity_id = str(payload["id"])
        assert await sync_state_repo.is_reported(db_session, user.id, activity_id)
        assert bot.sent == []

    async def test_no_active_plan_is_left_unreported_and_silent(self, db_session):
        user = await _make_user(db_session, 906)
        payload = _load("activity_full.json")
        client = _FakeClient(payload)
        bot = _FakeBot()

        await notify_detected_activity(db_session, user, bot, client, payload, announce=True)
        await db_session.commit()

        activity_id = str(payload["id"])
        assert not await sync_state_repo.is_reported(db_session, user.id, activity_id)
        assert bot.sent == []
