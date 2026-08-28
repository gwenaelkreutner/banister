"""spec 004 T018-T021 — every SessionSpec construction site in plan_modifier.py
either scales a session's steps consistently with its new summary, or — for a
legacy session with no steps — leaves it legacy rather than fabricating structure
(FR-012, FR-014).

No prior test file existed for plan_modifier.py (a pre-existing gap, not something
this feature introduced) — these tests cover the steps-consistency guarantee this
phase added, not the module's full pre-existing behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.engine.plan_builder import generate_plan
from app.engine.plan_modifier import (
    _apply_progressive_recovery,
    adapt_plan_for_injury,
    apply_proposed_modification,
    apply_session_adjustment,
    propose_session_adjustment,
    propose_week_adjustment,
)
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
    TrainingPlanSchema,
)


@dataclass
class _FakePlan:
    """Duck-typed stand-in for the ORM TrainingPlan — plan_modifier.py only reads
    .start_date and reads/writes .plan_technical."""
    plan_technical: dict
    start_date: date = field(default_factory=lambda: date.today() - timedelta(days=7))


def _real_structured_plan() -> tuple[dict, date]:
    """A real plan_builder.py output, not hand-written — every session carries
    steps produced by the actual generator (spec 004 Phase 3). Returns
    (plan_technical, start_date) — plan_modifier's week arithmetic is
    ((target_date - plan.start_date).days // 7 + 1), so the fixture's
    _FakePlan.start_date MUST be the plan's own start_date, not an
    independently guessed one, or every date computed against it lands in
    the wrong week."""
    profile = AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness", target_date=None),
        availability=AvailabilityProfile(
            hours_per_week=8, preferred_days=["tuesday", "thursday", "saturday", "sunday"]
        ),
        level="intermediate",
        structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
        physio=PhysioProfile(
            age=34, hr_max=186, hr_max_source="declared", hr_rest=58, hr_rest_source="declared"
        ),
        coaching_mode="power",
        health_constraints=False,
    )
    plan = generate_plan(profile)
    return plan.model_dump(mode="json"), plan.start_date


def _find_session_with_steps(plan_technical: dict) -> tuple[int, int]:
    """Returns (week_number, day_of_week) of the first session that has steps."""
    schema = TrainingPlanSchema.model_validate(plan_technical)
    for week in schema.weeks:
        for sess in week.sessions:
            if sess.steps is not None:
                return week.week_number, sess.day_of_week
    raise AssertionError("expected at least one structured session in the fixture plan")


def _all_sessions(plan_technical: dict):
    schema = TrainingPlanSchema.model_validate(plan_technical)
    return [s for week in schema.weeks for s in week.sessions]


class TestInjuryAdaptationPreservesSteps:
    def test_structured_sessions_keep_scaled_consistent_steps(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)
        week_num, _ = _find_session_with_steps(plan.plan_technical)

        result = adapt_plan_for_injury(plan, {"zone_restrictions": {"Z4": "Z2"}})
        assert result["modified_weeks"]

        schema = TrainingPlanSchema.model_validate(plan.plan_technical)
        target_week = next(w for w in schema.weeks if w.week_number == week_num)
        # Every session in the modified week must still be internally consistent —
        # the model_validator already enforces this at construction time (it would
        # have raised), but assert steps survived rather than silently vanishing.
        for sess in target_week.sessions:
            assert sess.steps is not None


class TestWeekAdjustmentThreadsStepsThroughTheProposalDict:
    def test_propose_includes_steps_in_after_sessions(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)

        proposal = propose_week_adjustment(
            plan, week_offset=0, modification_type="reduce_intensity"
        )
        assert "error" not in proposal
        assert any(s.get("steps") is not None for s in proposal["after_sessions"])

    def test_apply_reconstructs_steps_from_the_proposal(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)
        week_num = (date.today() - plan.start_date).days // 7 + 1

        proposal = propose_week_adjustment(
            plan, week_offset=0, modification_type="reduce_intensity"
        )
        ok = apply_proposed_modification(plan, proposal)
        assert ok is True

        schema = TrainingPlanSchema.model_validate(plan.plan_technical)
        target_week = next(w for w in schema.weeks if w.week_number == week_num)
        assert any(s.steps is not None for s in target_week.sessions)


class TestSessionAdjustmentPreservesOrScalesSteps:
    def test_indoor_action_keeps_steps_unchanged(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)
        week_num = (date.today() - plan.start_date).days // 7 + 1
        schema0 = TrainingPlanSchema.model_validate(plan.plan_technical)
        this_week = next(w for w in schema0.weeks if w.week_number == week_num)
        before = next(s for s in this_week.sessions if s.steps is not None)
        dow = before.day_of_week
        target_date = plan.start_date + timedelta(days=(week_num - 1) * 7 + dow)

        proposal = propose_session_adjustment(plan, target_date, "indoor", [])
        assert "no_session" not in proposal
        ok = apply_session_adjustment(plan, proposal)
        assert ok is True

        schema_after = TrainingPlanSchema.model_validate(plan.plan_technical)
        after = next(
            s for w in schema_after.weeks if w.week_number == week_num
            for s in w.sessions if s.day_of_week == dow
        )
        assert after.steps == before.steps

    def test_shift_action_keeps_steps_unchanged(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)
        week_num = (date.today() - plan.start_date).days // 7 + 1
        schema0 = TrainingPlanSchema.model_validate(plan.plan_technical)
        this_week = next(w for w in schema0.weeks if w.week_number == week_num)
        before = next(s for s in this_week.sessions if s.steps is not None)
        dow = before.day_of_week
        target_date = plan.start_date + timedelta(days=(week_num - 1) * 7 + dow)

        proposal = propose_session_adjustment(
            plan, target_date, "shift",
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
        )
        if proposal.get("no_session"):
            return  # no free slot available in this fixture — not what's under test
        ok = apply_session_adjustment(plan, proposal)
        assert ok is True

        schema_after = TrainingPlanSchema.model_validate(plan.plan_technical)
        new_day = proposal["session_after"]["day"]
        new_date = date.fromisoformat(proposal["session_after"]["date"])
        new_week_num = (new_date - plan.start_date).days // 7 + 1
        after = next(
            s for w in schema_after.weeks if w.week_number == new_week_num
            for s in w.sessions if s.day_of_week == new_day
        )
        assert after.steps == before.steps

    def test_reduce_50_scales_steps_consistently(self):
        plan_technical, start_date = _real_structured_plan()
        plan = _FakePlan(plan_technical=plan_technical, start_date=start_date)
        week_num = (date.today() - plan.start_date).days // 7 + 1
        schema0 = TrainingPlanSchema.model_validate(plan.plan_technical)
        this_week = next(w for w in schema0.weeks if w.week_number == week_num)
        before = next(s for s in this_week.sessions if s.steps is not None)
        dow = before.day_of_week
        target_date = plan.start_date + timedelta(days=(week_num - 1) * 7 + dow)

        proposal = propose_session_adjustment(plan, target_date, "reduce_50", [])
        assert "no_session" not in proposal
        ok = apply_session_adjustment(plan, proposal)
        assert ok is True

        schema_after = TrainingPlanSchema.model_validate(plan.plan_technical)
        after = next(
            s for w in schema_after.weeks if w.week_number == week_num
            for s in w.sessions if s.day_of_week == dow
        )
        # The model_validator already enforces internal consistency at construction
        # time; assert steps survived the reduction rather than being dropped.
        assert after.steps is not None


class TestProgressiveRecoveryPreservesSteps:
    def test_following_weeks_keep_scaled_consistent_steps(self):
        plan_technical, _ = _real_structured_plan()
        schema = TrainingPlanSchema.model_validate(plan_technical)
        base_week = schema.weeks[0].week_number

        _apply_progressive_recovery(schema, base_week)

        for offset in (1, 2):
            target = next((w for w in schema.weeks if w.week_number == base_week + offset), None)
            if target is None:
                continue
            for sess in target.sessions:
                assert sess.steps is not None


class TestLegacySessionsStayLegacyThroughModification:
    """FR-014: a modification applied to a session with no steps must not fabricate
    structure for it — the structure was never known and inventing it would be the
    silent estimation Constitution Principle IV forbids."""

    def _legacy_plan(self) -> dict:
        return {
            "weeks": [{
                "week_number": 1,
                "phase": "build",
                "is_recovery_week": False,
                "total_tss_target": 200.0,
                "sessions": [{
                    "day_of_week": 2,
                    "workout_type": "endurance",
                    "zone_code": "Z2",
                    "duration_minutes": 90,
                    "target_time_in_zone_minutes": 0,
                    "tss_target": 80.0,
                    "description_fr": "Endurance Z2",
                }],
                "start_date": str(date.today()),
            }],
            "zones": {},
            "initial_weekly_tss": 200,
            "peak_weekly_tss": 300,
            "weeks_count": 1,
            "coaching_mode": "power",
        }

    def test_injury_adaptation_on_legacy_session_stays_legacy(self):
        # start_date = today so "today" falls inside this plan's only week (1).
        plan = _FakePlan(plan_technical=self._legacy_plan(), start_date=date.today())
        adapt_plan_for_injury(plan, {"zone_restrictions": {}})
        schema = TrainingPlanSchema.model_validate(plan.plan_technical)
        sess = schema.weeks[0].sessions[0]
        assert sess.steps is None

    def test_week_adjustment_on_legacy_session_stays_legacy(self):
        plan = _FakePlan(plan_technical=self._legacy_plan(), start_date=date.today())
        proposal = propose_week_adjustment(
            plan, week_offset=0, modification_type="reduce_intensity"
        )
        assert proposal["after_sessions"][0].get("steps") is None
        apply_proposed_modification(plan, proposal)
        schema = TrainingPlanSchema.model_validate(plan.plan_technical)
        assert schema.weeks[0].sessions[0].steps is None
