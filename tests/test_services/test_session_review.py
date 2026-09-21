"""assemble_review_context() (app/services/session_review.py) — assemblage déterministe
pour /review, zéro LLM. Aiogram-free, comme test_activity_feedback.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.db.models.wellness import Wellness
from app.db.repositories import plan_repo
from app.services.session_review import assemble_review_context


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _plan_technical() -> dict:
    return {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": 300.0,
                "sessions": [
                    {
                        "day_of_week": 2,
                        "workout_type": "endurance",
                        "zone_code": "Z2",
                        "duration_minutes": 60,
                        "target_time_in_zone_minutes": 50,
                        "tss_target": 55.0,
                        "description_fr": "Endurance Z2",
                    },
                ],
            },
        ],
        "zones": {},
        "initial_weekly_tss": 300.0,
        "peak_weekly_tss": 400.0,
        "weeks_count": 1,
        "coaching_mode": "power",
    }


async def test_resolves_the_planned_session_when_the_log_is_linked_to_a_plan(db_session):
    user = await _make_user(db_session, 9001)
    plan = await plan_repo.create(
        db_session, user_id=user.id, plan_technical=_plan_technical(),
        start_date=date.today(), end_date=date.today(),
    )
    log = SessionLog(
        user_id=user.id, plan_id=plan.id, week_number=1, day_of_week=2,
        logged_date=date.today(), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.session_spec is not None
    assert ctx.session_spec.workout_type == "endurance"


async def test_resolves_a_deactivated_plan_the_log_still_points_to(db_session):
    """Un /goal ultérieur désactive l'ancien plan — le log qui pointe dessus doit encore
    résoudre sa séance planifiée (plan_repo.get_by_id, pas get_active_plan)."""
    user = await _make_user(db_session, 9002)
    plan = await plan_repo.create(
        db_session, user_id=user.id, plan_technical=_plan_technical(),
        start_date=date.today(), end_date=date.today(),
    )
    await plan_repo.deactivate_all_for_user(db_session, user.id)
    log = SessionLog(
        user_id=user.id, plan_id=plan.id, week_number=1, day_of_week=2,
        logged_date=date.today(), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.session_spec is not None


async def test_freestyle_log_has_no_session_spec(db_session):
    user = await _make_user(db_session, 9003)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="unplanned", tss_actual=40.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.session_spec is None


async def test_fitness_at_session_uses_the_stored_snapshot_not_current_fitness(db_session):
    user = await _make_user(db_session, 9004)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="done", tss_actual=60.0,
        ctl_at_session=50.0, atl_at_session=60.0, tsb_at_session=-10.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.fitness_at_session is not None
    assert ctx.fitness_at_session.ctl == 50.0
    assert ctx.fitness_at_session.atl == 60.0
    assert ctx.fitness_at_session.tsb == -10.0


async def test_fitness_at_session_is_none_without_a_full_snapshot(db_session):
    user = await _make_user(db_session, 9005)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.fitness_at_session is None


async def test_weekly_snapshot_is_always_populated(db_session):
    user = await _make_user(db_session, 9006)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.weekly_snapshot is not None


async def test_tid_is_none_without_zone_data(db_session):
    user = await _make_user(db_session, 9007)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.tid is None


async def test_tid_is_computed_from_time_in_zones(db_session):
    user = await _make_user(db_session, 9008)
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=date.today(), status="done", tss_actual=60.0,
        time_in_zones_s={"Z1": 8000, "Z3": 500, "Z5": 1500},
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.tid is not None
    assert ctx.tid.classification == "polarized"


async def test_recovery_index_is_computed_at_the_session_date_not_today(db_session):
    """recovery_index must reflect the athlete's state on the logged session's own date —
    never "today" (app/llm/chat.py's own usage) — that would be a different day's
    recovery state attached to a past session's review."""
    user = await _make_user(db_session, 9009)
    session_date = date.today() - timedelta(days=10)
    for offset in range(1, 6):
        db_session.add(
            Wellness(
                user_id=user.id, date=session_date - timedelta(days=offset),
                hrv=60.0, resting_hr=50,
            )
        )
    db_session.add(Wellness(user_id=user.id, date=session_date, hrv=54.0, resting_hr=50))
    # Une lecture "aujourd'hui" volontairement différente — ne doit jamais fuiter dans le
    # calcul si le wiring utilise bien log.logged_date.
    db_session.add(Wellness(user_id=user.id, date=date.today(), hrv=30.0, resting_hr=70))
    log = SessionLog(
        user_id=user.id, plan_id=None, week_number=None, day_of_week=None,
        logged_date=session_date, status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.recovery_index is not None
    assert ctx.recovery_index == pytest.approx(0.9, abs=0.01)


async def test_detected_phase_uses_the_sessions_own_plan_week_not_todays(db_session):
    """plan_week_phase must come from the plan week the LOGGED SESSION belonged to
    (log.week_number), not whatever week the plan is on today."""
    user = await _make_user(db_session, 9010)
    plan_technical = _plan_technical()
    plan_technical["weeks"][0]["phase"] = "peak"
    plan = await plan_repo.create(
        db_session, user_id=user.id, plan_technical=plan_technical,
        start_date=date.today(), end_date=date.today(),
    )
    log = SessionLog(
        user_id=user.id, plan_id=plan.id, week_number=1, day_of_week=2,
        logged_date=date.today() - timedelta(days=30), status="done", tss_actual=60.0,
    )
    db_session.add(log)
    await db_session.flush()

    ctx = await assemble_review_context(db_session, user, log)

    assert ctx.detected_phase is not None
    assert ctx.detected_phase.secondary_phase == "peak"
