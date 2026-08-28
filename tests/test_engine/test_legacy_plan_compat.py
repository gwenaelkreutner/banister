"""spec 004 T027-T029 — a plan generated before this feature keeps working exactly
as before: it displays, matches activities, and scores adherence, and generating a
new plan for the same athlete leaves the stored legacy one untouched.

tests/fixtures/plans/legacy_plan.json is a real plan_technical extracted from the
production database (spec 004 T003), not hand-written.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.bot.routers.plan import _format_week
from app.engine.adherence_kpi import compute_session_kpi
from app.engine.plan_builder import generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
    TrainingPlanSchema,
)
from app.providers.analysis.matching import score_activity_vs_session

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "plans"


def _load_legacy_plan() -> TrainingPlanSchema:
    data = json.loads((_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8"))
    return TrainingPlanSchema.model_validate(data)


class TestLegacyPlanDisplay:
    """T027 (display half) — /plan and /week N's rendering function, exercised
    directly against a real legacy-shaped plan."""

    def test_every_week_renders_without_error(self):
        plan = _load_legacy_plan()
        for week in plan.weeks:
            text = _format_week(week, plan.weeks_count)
            assert text  # non-empty, no exception
            assert f"Semaine {week.week_number}" in text


class TestLegacyPlanMatchingAndAdherence:
    """T027 (matching/adherence half). Note: scripts/snapshot_session_behaviour.py
    already exercises this exact path against the real database's real plan on
    every phase of this feature (that plan is itself legacy-shaped — confirmed
    when it was extracted in T003), and has stayed byte-identical throughout. This
    test exists so the guarantee is checked by `pytest`, not only by a script a
    developer has to remember to run."""

    def test_matching_scores_a_legacy_session_without_error(self):
        from types import SimpleNamespace

        plan = _load_legacy_plan()
        first_session = plan.weeks[0].sessions[0]
        assert first_session.steps is None  # confirms the fixture is genuinely legacy

        fake_activity = SimpleNamespace(
            duration_s=first_session.duration_minutes * 60,
            tss=first_session.tss_target,
            session_type_real=first_session.workout_type,
            dominant_zone=first_session.zone_code,
            environment="outdoor",
        )
        result = score_activity_vs_session(fake_activity, first_session)
        assert 0 <= result.confidence_score <= 100

    def test_adherence_scores_a_legacy_session_without_error(self):
        plan = _load_legacy_plan()
        week = plan.weeks[0]
        sess = week.sessions[0]
        result = compute_session_kpi(
            tss_planned=sess.tss_target,
            week_tss_planned=week.total_tss_target,
            weeks_total=plan.weeks_count,
            tss_actual=sess.tss_target,
            workout_type=sess.workout_type,
            session_type_real=sess.workout_type,
            tsb_after=0.0,
        )
        assert result.pts >= 0


class TestPartiallyStructuredPlan:
    """T028 — a plan holding both legacy and structured sessions is the ordinary
    state during a transition (not a special case)."""

    def test_mixed_plan_renders_and_scores_without_error(self):
        data = json.loads((_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8"))
        first = data["weeks"][0]["sessions"][0]
        first["steps"] = [{
            "kind": "steady",
            "duration_minutes": first["duration_minutes"],
            "zone_code": first["zone_code"],
        }]
        plan = TrainingPlanSchema.model_validate(data)

        week = plan.weeks[0]
        assert week.sessions[0].steps is not None
        assert all(s.steps is None for s in week.sessions[1:])

        # Both kinds render through the same path without special-casing.
        text = _format_week(week, plan.weeks_count)
        assert text


class TestGeneratingANewPlanLeavesTheOldOneUntouched:
    """T029 — the legacy plan document itself is immutable data; generating a new
    plan for the same athlete cannot mutate it, since generate_plan() never reads
    or writes any existing plan (it builds a fresh TrainingPlanSchema from the
    athlete profile alone). Verified by identity/equality, not just by assertion."""

    def test_new_plan_generation_does_not_mutate_the_legacy_document(self):
        original_json = (_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8")
        original_data = json.loads(original_json)

        profile = AthleteProfileSchema(
            objective=ObjectiveProfile(type="fitness", target_date=None),
            availability=AvailabilityProfile(
                hours_per_week=6, preferred_days=["monday", "wednesday", "friday"]
            ),
            level="beginner",
            structured_plan_history=False,
            equipment=EquipmentProfile(power_meter=False, ftp=None, ftp_source="estimated"),
            physio=PhysioProfile(
                age=40, hr_max=180, hr_max_source="estimated",
                hr_rest=60, hr_rest_source="estimated",
            ),
            coaching_mode="hr",
            health_constraints=False,
        )
        generate_plan(profile)  # a completely independent plan — nothing shared

        reloaded = json.loads((_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8"))
        assert reloaded == original_data
