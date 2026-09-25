from app.bot.routers.plan import _build_overview, _format_week
from app.config import settings
from app.engine.schemas import SessionSpec, Step, TrainingPlanSchema, WeekPlan


def _plan():
    week = WeekPlan(
        week_number=1,
        phase="base",
        is_recovery_week=False,
        total_tss_target=40.0,
        sessions=[
            SessionSpec(
                day_of_week=0,
                workout_type="endurance",
                zone_code="Z2",
                duration_minutes=60,
                target_time_in_zone_minutes=0,
                tss_target=40.0,
                description_fr="Endurance Z2 — ancienne note française",
                steps=[Step(kind="steady", duration_minutes=60, zone_code="Z2")],
            )
        ],
    )
    return TrainingPlanSchema(
        weeks=[week],
        zones={},
        initial_weekly_tss=40.0,
        peak_weekly_tss=40.0,
        weeks_count=1,
        coaching_mode="power",
    )


def test_plan_display_uses_selected_language_without_mutating_plan(monkeypatch):
    plan = _plan()
    original = plan.model_dump()

    monkeypatch.setattr(settings, "app_language", "en")
    english = _format_week(plan.weeks[0], 1, plan.coaching_mode)
    overview_en = _build_overview(plan, "")
    assert "Week 1/1" in english
    assert "Mon" in english
    assert "ancienne note" not in english
    assert "Endurance Z2" in english
    assert "1-week plan" in overview_en

    monkeypatch.setattr(settings, "app_language", "fr")
    french = _format_week(plan.weeks[0], 1, plan.coaching_mode)
    overview_fr = _build_overview(plan, "")
    assert "Semaine 1/1" in french
    assert "ancienne note française" in french
    assert "Plan 1 semaines" in overview_fr
    assert plan.model_dump() == original
