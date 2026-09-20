"""session_at() — point d'entrée unique pour "chercher la semaine puis le jour dedans",
qui existait dupliqué à la main à ~15 endroits du repo avant cette extraction (voir
app/engine/schemas.py). Introduit pour /review, sans migrer les sites existants."""
from __future__ import annotations

from app.engine.schemas import SessionSpec, TrainingPlanSchema, WeekPlan, session_at


def _make_plan() -> TrainingPlanSchema:
    week1 = WeekPlan(
        week_number=1,
        phase="base",
        is_recovery_week=False,
        total_tss_target=300.0,
        sessions=[
            SessionSpec(
                day_of_week=2,
                workout_type="endurance",
                zone_code="Z2",
                duration_minutes=60,
                target_time_in_zone_minutes=50,
                tss_target=55.0,
                description_fr="Endurance Z2",
            ),
        ],
    )
    return TrainingPlanSchema(
        weeks=[week1],
        zones={},
        initial_weekly_tss=300.0,
        peak_weekly_tss=400.0,
        weeks_count=1,
        coaching_mode="power",
    )


def test_session_at_finds_the_matching_week_and_day():
    plan = _make_plan()
    spec = session_at(plan, week_number=1, day_of_week=2)
    assert spec is not None
    assert spec.workout_type == "endurance"


def test_session_at_returns_none_when_week_is_absent():
    plan = _make_plan()
    assert session_at(plan, week_number=99, day_of_week=2) is None


def test_session_at_returns_none_when_day_is_absent_from_an_existing_week():
    plan = _make_plan()
    assert session_at(plan, week_number=1, day_of_week=5) is None
