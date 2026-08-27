"""Post-activity context assembly tests (spec 002 T050, FR-038).

No aiogram anywhere here — assemble_activity_feedback() is exercised directly against a
real (in-memory) DB session, proving FR-038's actual point: this logic is testable
without simulating a Telegram conversation.
"""
from __future__ import annotations

from datetime import date, datetime

from app.db.models.user import User
from app.db.repositories import plan_repo
from app.services.activity_feedback import assemble_activity_feedback
from app.strava.analysis_models import AnalyzedSession


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _plan_technical(*, tss_target: float = 70, target_time_in_zone_minutes: int = 60) -> dict:
    return {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": tss_target,
                "sessions": [
                    {
                        "day_of_week": 0,  # Monday
                        "workout_type": "endurance",
                        "zone_code": "Z2",
                        "duration_minutes": 90,
                        "target_time_in_zone_minutes": target_time_in_zone_minutes,
                        "tss_target": tss_target,
                        "description_fr": "Sortie endurance",
                    }
                ],
            }
        ],
        "zones": {
            "Z2": {
                "name": "Endurance",
                "code": "Z2",
                "lower_pct": 0.56,
                "upper_pct": 0.75,
                "description_fr": "Endurance aérobie",
            }
        },
        "initial_weekly_tss": 200,
        "peak_weekly_tss": 350,
        "weeks_count": 1,
        "coaching_mode": "power",
    }


async def _make_plan(session, user_id, *, start: date, **kwargs):
    return await plan_repo.create(
        session,
        user_id,
        plan_technical=_plan_technical(**kwargs),
        start_date=start,
        end_date=start,
    )


def _analyzed(
    *,
    duration_s: int = 90 * 60,
    tss: float | None = 72.0,
    session_type_real: str = "endurance",
    dominant_zone: str | None = "Z2",
    environment: str = "outdoor",
    time_in_zones_s: dict | None = None,
) -> AnalyzedSession:
    return AnalyzedSession(
        session_id="i-test-1",
        source="intervals_icu",
        sport_type="ride",
        start_datetime=datetime(2026, 1, 5, 10, 0),  # a Monday
        duration_s=duration_s,
        has_power=False,
        has_heartrate=True,
        has_gps=True,
        tss=tss,
        session_type_real=session_type_real,
        dominant_zone=dominant_zone,
        environment=environment,
        time_in_zones_s=time_in_zones_s or {},
    )


_MONDAY = date(2026, 1, 5)


class TestNoActivePlan:
    async def test_no_plan_returns_no_plan_outcome(self, db_session):
        user = await _make_user(db_session, 800)

        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert result.outcome == "no_plan"
        assert result.log is None


class TestMatchedActivity:
    async def test_matched_activity_creates_a_done_log(self, db_session):
        user = await _make_user(db_session, 801)
        await _make_plan(db_session, user.id, start=_MONDAY)

        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert result.outcome == "matched"
        assert result.log is not None
        assert result.log.status == "done"
        assert result.log.source == "intervals_icu"
        assert result.log.strava_activity_id == "i-test-1"
        assert result.highlight is not None
        assert result.match_result is not None
        assert result.match_result.is_aligned

    async def test_kpi_contribution_is_computed(self, db_session):
        user = await _make_user(db_session, 802)
        await _make_plan(db_session, user.id, start=_MONDAY)

        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert result.kpi_contribution is not None
        assert result.log.kpi_contribution == result.kpi_contribution

    async def test_reanalyze_with_plan_is_called_with_seconds_not_minutes(self, db_session):
        """The planned zone target is stored in minutes (SessionSpec.target_time_in_zone_minutes)
        but respect_zones_score compares against seconds-in-zone — the caller-supplied
        reanalyze callback must receive seconds, not raw minutes."""
        user = await _make_user(db_session, 803)
        await _make_plan(db_session, user.id, start=_MONDAY, target_time_in_zone_minutes=60)

        seen = {}

        def reanalyze(planned_zone: str, planned_target_time_in_zone_s: int) -> AnalyzedSession:
            seen["zone"] = planned_zone
            seen["seconds"] = planned_target_time_in_zone_s
            return _analyzed(time_in_zones_s={"Z2": 5000})

        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-test-1",
            reanalyze_with_plan=reanalyze,
        )

        assert seen["zone"] == "Z2"
        assert seen["seconds"] == 60 * 60  # 60 minutes -> 3600 seconds, not 60
        assert result.log.time_in_zones_s == {"Z2": 5000}


class TestUnplannedAndBonus:
    async def test_no_candidate_in_window_is_unplanned(self, db_session):
        user = await _make_user(db_session, 804)
        await _make_plan(db_session, user.id, start=_MONDAY)

        # Five days away from the only planned session — outside the ±2 day window
        far_date = date(2026, 1, 10)
        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            far_date,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert result.outcome == "unplanned"
        assert result.log is not None
        assert result.log.status == "unplanned"
        assert result.reason is not None

    async def test_bonus_when_the_slot_is_already_taken(self, db_session):
        user = await _make_user(db_session, 805)
        await _make_plan(db_session, user.id, start=_MONDAY)

        # First activity claims the only planned session for the week
        first = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-first",
        )
        assert first.outcome == "matched"
        await db_session.commit()

        # Second activity, same day, same plan slot already claimed -> bonus, not an error
        second = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-second",
        )

        assert second.outcome == "bonus"
        assert second.log is not None
        assert second.log.status == "unplanned"

    async def test_bonus_and_unplanned_never_carry_an_error_framing(self, db_session):
        """FR-034: training outside the plan must never read as an error."""
        user = await _make_user(db_session, 806)
        await _make_plan(db_session, user.id, start=_MONDAY)

        far_date = date(2026, 1, 10)
        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(),
            far_date,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert "erreur" not in (result.reason or "").lower()
        assert "error" not in (result.reason or "").lower()


class TestRetryAfterPartialFailure:
    """Simulates the scenario FR-012 exists for: a first call ingests the activity (the
    SessionLog gets committed) but delivery never got marked reported — e.g. Telegram
    was briefly unreachable. A retry must reuse the existing log, not create a second
    one for the same activity."""

    async def test_second_call_reuses_the_existing_log_not_a_duplicate(self, db_session):
        user = await _make_user(db_session, 808)
        await _make_plan(db_session, user.id, start=_MONDAY)

        first = await assemble_activity_feedback(
            db_session, user, _analyzed(), _MONDAY,
            source="intervals_icu", source_activity_id="i-retry-1",
        )
        await db_session.commit()

        second = await assemble_activity_feedback(
            db_session, user, _analyzed(), _MONDAY,
            source="intervals_icu", source_activity_id="i-retry-1",
        )

        assert second.outcome == "matched"
        assert second.log.id == first.log.id

        from sqlalchemy import func, select

        from app.db.models.session_log import SessionLog

        count = await db_session.scalar(
            select(func.count()).select_from(SessionLog).where(
                SessionLog.user_id == user.id, SessionLog.strava_activity_id == "i-retry-1"
            )
        )
        assert count == 1

    async def test_reused_context_still_carries_a_highlight_to_resend(self, db_session):
        user = await _make_user(db_session, 809)
        await _make_plan(db_session, user.id, start=_MONDAY)

        await assemble_activity_feedback(
            db_session, user, _analyzed(), _MONDAY,
            source="intervals_icu", source_activity_id="i-retry-2",
        )
        await db_session.commit()

        second = await assemble_activity_feedback(
            db_session, user, _analyzed(), _MONDAY,
            source="intervals_icu", source_activity_id="i-retry-2",
        )

        assert second.highlight is not None


class TestNullTss:
    async def test_matched_activity_with_null_tss_does_not_crash(self, db_session):
        """spec 002: the source can genuinely have no computed load for an activity
        (~15% of real activities, research R9c). The whole assembly pipeline —
        matching, KPI, highlight selection — must handle that, not just the mapper."""
        user = await _make_user(db_session, 807)
        await _make_plan(db_session, user.id, start=_MONDAY)

        result = await assemble_activity_feedback(
            db_session,
            user,
            _analyzed(tss=None),
            _MONDAY,
            source="intervals_icu",
            source_activity_id="i-test-1",
        )

        assert result.outcome == "matched"
        assert result.log.tss_actual is None
        assert result.highlight is not None
