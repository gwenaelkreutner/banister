"""spec 004 T016 — every session of a freshly generated plan carries steps whose
summary agrees with them (SC-001, SC-002), and repeated efforts are RepeatGroups,
never duplicated flat steps (FR-003)."""
from __future__ import annotations

from app.engine.plan_builder import generate_plan
from app.engine.schemas import RepeatGroup, Step, derive_duration_minutes
from tests.test_engine.test_plan_builder import make_profile


def _all_sessions(plan):
    return [s for week in plan.weeks for s in week.sessions]


class TestEverySessionHasSteps:
    def test_power_mode_plan(self):
        plan = generate_plan(make_profile(power_meter=True))
        sessions = _all_sessions(plan)
        assert sessions, "expected at least one session"
        for s in sessions:
            assert s.steps is not None, f"session on day {s.day_of_week} has no steps"

    def test_hr_mode_plan(self):
        plan = generate_plan(make_profile(power_meter=False))
        for s in _all_sessions(plan):
            assert s.steps is not None

    def test_beginner_recovery_weeks_have_steps(self):
        """Recovery weeks are a separate code path in _build_sessions() — must not
        be forgotten."""
        plan = generate_plan(make_profile(level="beginner", hours=8))
        recovery_weeks = [w for w in plan.weeks if w.is_recovery_week]
        assert recovery_weeks, "expected at least one recovery week in this plan"
        for week in recovery_weeks:
            for s in week.sessions:
                assert s.steps is not None


class TestStepsAgreeWithSummary:
    """The Pydantic model_validator (Phase 2) already enforces this at construction
    time — generate_plan() would have raised if any session disagreed. These tests
    exist so a *regression* that silently stops attaching steps (steps=None on
    everything) doesn't slip through by producing a plan that trivially validates."""

    def test_every_session_duration_equals_sum_of_its_own_steps(self):
        plan = generate_plan(make_profile())
        for s in _all_sessions(plan):
            assert s.duration_minutes == derive_duration_minutes(s.steps)

    def test_steady_sessions_are_a_single_step(self):
        plan = generate_plan(make_profile())
        for s in _all_sessions(plan):
            if s.workout_type in ("long_ride", "endurance", "recovery"):
                # long_ride's race-week marker and race-week endurance are also
                # steady; the taper/recovery Z6 sprint is workout_type="intervals"
                # so it's excluded here correctly.
                assert len(s.steps) == 1
                assert s.steps[0].kind == "steady"


class TestRepeatedEffortsAreRepeatGroups:
    def test_at_least_one_interval_session_uses_a_repeat_group(self):
        """FR-003: repeated efforts MUST be a RepeatGroup, never duplicated flat
        steps. A 12-week plan is virtually certain to include at least one
        sweet-spot/threshold/VO2 session."""
        plan = generate_plan(make_profile(target_weeks=12))
        interval_sessions = [s for s in _all_sessions(plan) if s.workout_type == "intervals"]
        assert interval_sessions, "expected at least one interval session in a 12-week plan"

        has_repeat_group = any(
            any(isinstance(item, RepeatGroup) for item in s.steps)
            for s in interval_sessions
        )
        assert has_repeat_group

    def test_repeat_group_never_duplicates_the_work_step_flatly(self):
        """No interval session should represent 'N x work' as N separate top-level
        Step entries instead of one RepeatGroup(repeat=N, ...)."""
        plan = generate_plan(make_profile(target_weeks=12))
        for s in _all_sessions(plan):
            if s.workout_type != "intervals" or s.steps is None:
                continue
            top_level_work_steps = [
                item for item in s.steps if isinstance(item, Step) and item.kind == "work"
            ]
            # A genuinely single-effort interval session (none exist in the current
            # library, but the assertion should hold if one is ever added) is fine
            # with exactly one top-level work step; more than one is the forbidden
            # "duplicated steps" shape.
            assert len(top_level_work_steps) <= 1, (
                f"session on day {s.day_of_week} has {len(top_level_work_steps)} "
                "top-level 'work' steps — repeated efforts must be a RepeatGroup"
            )
