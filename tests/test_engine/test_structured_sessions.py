"""Structured session schema tests (spec 004 T010, T011).

Duration-equals-sum is asserted against independently stated totals below, never
against the derivation itself — a test that computes the expected value the same way
the code does would pass even if both were wrong the same way.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.engine.schemas import (
    RepeatGroup,
    SessionSpec,
    Step,
    TrainingPlanSchema,
    derive_duration_minutes,
    derive_target_time_in_zone_minutes,
    derive_zone_code,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "plans"


# ── Threshold 3×12r4: 15 (warmup) + 3×12 (work) + 2×4 (recovery) + 15 (cooldown) ──
# = 15 + 36 + 8 + 15 = 74 minutes. Written out by hand, not derived, so the test can
# actually fail if the derivation logic is wrong.

def _threshold_3x12_steps() -> list[Step | RepeatGroup]:
    return [
        Step(kind="warmup", duration_minutes=15, zone_code="Z1"),
        RepeatGroup(repeat=3, steps=[
            Step(kind="work", duration_minutes=12, zone_code="Z4"),
            Step(kind="recovery", duration_minutes=4, zone_code="Z1"),
        ]),
        Step(kind="cooldown", duration_minutes=15, zone_code="Z1"),
    ]


class TestDerivation:
    def test_duration_sums_steps_and_repeat_group_multiplies(self):
        """warmup 15 + 3×(work 12 + recovery 4) + cooldown 15 = 15 + 48 + 15 = 78.

        Deliberately includes a recovery after the final repetition, unlike
        plan_builder's old `warmup + sets*work + (sets-1)*rest + cooldown` formula,
        which dropped the trailing rest. FR-003 asks for one repeated unit with a
        repetition count, not a special-cased last repetition — and the old formula's
        omission was an unexamined convenience baked into a clamp research R2 already
        found to be a latent defect, not a training requirement. Total duration/TSS
        moves slightly upward as a result; validated for quality against the eval
        harness rather than assumed (research §Open questions item 1)."""
        steps = _threshold_3x12_steps()
        assert derive_duration_minutes(steps) == 15 + 3 * (12 + 4) + 15  # = 78

    def test_zone_code_is_the_work_zone(self):
        assert derive_zone_code(_threshold_3x12_steps()) == "Z4"

    def test_zone_code_falls_back_to_steady_when_no_work_step(self):
        steady = [Step(kind="steady", duration_minutes=90, zone_code="Z2")]
        assert derive_zone_code(steady) == "Z2"

    def test_target_time_in_zone_counts_only_work_steps(self):
        assert derive_target_time_in_zone_minutes(_threshold_3x12_steps()) == 36  # 3×12

    def test_target_time_in_zone_is_zero_for_steady_session(self):
        """Matches plan_builder's existing behaviour: only 'intervals' workout_type
        sessions have a nonzero time-in-zone target (FR-008 compatibility)."""
        steady = [Step(kind="steady", duration_minutes=90, zone_code="Z2")]
        assert derive_target_time_in_zone_minutes(steady) == 0


class TestRepeatGroupValidation:
    def test_repeat_of_one_is_rejected(self):
        with pytest.raises(ValidationError, match="repeat"):
            RepeatGroup(repeat=1, steps=[Step(kind="work", duration_minutes=10, zone_code="Z4")])

    def test_empty_steps_is_rejected(self):
        with pytest.raises(ValidationError):
            RepeatGroup(repeat=2, steps=[])

    def test_zero_length_step_is_rejected(self):
        with pytest.raises(ValidationError, match="duration_minutes"):
            Step(kind="work", duration_minutes=0, zone_code="Z4")


class TestSessionSpecStepsConsistency:
    def test_valid_structured_session_loads(self):
        spec = SessionSpec(
            day_of_week=2,
            workout_type="intervals",
            zone_code="Z4",
            duration_minutes=78,
            target_time_in_zone_minutes=36,
            tss_target=80.0,
            description_fr="Threshold 3x12",
            steps=_threshold_3x12_steps(),
        )
        assert spec.duration_minutes == 78

    def test_duration_disagreement_is_rejected_at_load_time(self):
        with pytest.raises(ValidationError, match="duration_minutes"):
            SessionSpec(
                day_of_week=2,
                workout_type="intervals",
                zone_code="Z4",
                duration_minutes=999,  # wrong on purpose
                target_time_in_zone_minutes=36,
                tss_target=80.0,
                description_fr="Threshold 3x12",
                steps=_threshold_3x12_steps(),
            )

    def test_zone_disagreement_is_rejected_at_load_time(self):
        with pytest.raises(ValidationError, match="zone_code"):
            SessionSpec(
                day_of_week=2,
                workout_type="intervals",
                zone_code="Z9",  # wrong on purpose
                duration_minutes=78,
                target_time_in_zone_minutes=36,
                tss_target=80.0,
                description_fr="Threshold 3x12",
                steps=_threshold_3x12_steps(),
            )

    def test_target_time_in_zone_disagreement_is_rejected_at_load_time(self):
        with pytest.raises(ValidationError, match="target_time_in_zone_minutes"):
            SessionSpec(
                day_of_week=2,
                workout_type="intervals",
                zone_code="Z4",
                duration_minutes=78,
                target_time_in_zone_minutes=999,  # wrong on purpose
                tss_target=80.0,
                description_fr="Threshold 3x12",
                steps=_threshold_3x12_steps(),
            )


class TestLegacySessionsWithoutSteps:
    """FR-013/FR-014: a session loaded without steps validates successfully and keeps
    its stored summary verbatim; nothing is fabricated."""

    def test_legacy_session_with_no_steps_loads(self):
        spec = SessionSpec(
            day_of_week=5,
            workout_type="endurance",
            zone_code="Z3",
            duration_minutes=270,
            target_time_in_zone_minutes=0,
            tss_target=295.2,
            description_fr="Endurance Z3",
        )
        assert spec.steps is None

    def test_legacy_session_summary_is_trusted_even_if_it_would_disagree_with_a_guess(self):
        """No validator runs on the summary when steps are absent — there is nothing
        to check it against, and inventing steps to check against would itself be the
        fabrication FR-014 forbids."""
        spec = SessionSpec(
            day_of_week=5,
            workout_type="endurance",
            zone_code="Z3",
            duration_minutes=999,  # would be "wrong" against nothing — no steps to compare
            target_time_in_zone_minutes=0,
            tss_target=295.2,
            description_fr="Endurance Z3",
        )
        assert spec.duration_minutes == 999

    def test_real_legacy_plan_fixture_loads_and_every_session_has_no_steps(self):
        """Fixture taken from the real database (spec 004 T003), not hand-written —
        a hand-written approximation would encode the same assumptions this code makes."""
        data = json.loads((_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8"))
        plan = TrainingPlanSchema.model_validate(data)
        assert len(plan.weeks) > 0
        for week in plan.weeks:
            for session in week.sessions:
                assert session.steps is None

    def test_plan_with_both_legacy_and_structured_sessions_loads(self):
        """The ordinary state during a transition (spec 004 US5 edge case) — no
        special handling required beyond per-session nullability."""
        data = json.loads((_FIXTURES / "legacy_plan.json").read_text(encoding="utf-8"))
        # Graft one structured session onto an otherwise-legacy plan.
        data["weeks"][0]["sessions"][0]["steps"] = [
            {"kind": "steady", "duration_minutes": data["weeks"][0]["sessions"][0]["duration_minutes"], "zone_code": data["weeks"][0]["sessions"][0]["zone_code"]}
        ]
        plan = TrainingPlanSchema.model_validate(data)
        first = plan.weeks[0].sessions[0]
        rest = plan.weeks[0].sessions[1:]
        assert first.steps is not None
        assert all(s.steps is None for s in rest)
