"""spec 009 US1 — freestyle session selection replaces periodization phase with
fitness state. No plan, no phase, ever referenced (SC-005); never guesses when
fitness is unavailable (FR-011, tested at the tool layer since this module always
receives an already-resolved FitnessMetrics).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.engine.atl_ctl import FitnessMetrics
from app.engine.freestyle_selector import (
    LONG_RIDE_MIN_MINUTES,
    RETURN_FROM_BREAK_GAP_DAYS,
    RETURN_FROM_BREAK_WINDOW_DAYS,
    NoSuitableTemplateError,
    build_freestyle_suggestion,
    choose_workout_type,
    days_since_hard_effort,
    days_since_return_from_break,
)
from app.engine.weekly_snapshot import WeeklySnapshot

_SNAPSHOT = WeeklySnapshot(
    tss_7d=250.0, tss_6w_avg=240.0, load_trend_pct=4.0, sessions_done_7d=3, monotony_index=1.4
)


@dataclass
class _FakeLog:
    logged_date: date
    tss_actual: float
    duration_minutes_actual: int


@dataclass
class _FakeActivity:
    activity_date: date
    tss: float
    duration_seconds: int


class TestDaysSinceHardEffort:
    def test_no_items_returns_none(self):
        assert days_since_hard_effort([], date(2026, 9, 18)) is None

    def test_only_easy_rides_returns_none(self):
        items = [_FakeLog(logged_date=date(2026, 9, 17), tss_actual=50, duration_minutes_actual=90)]
        assert days_since_hard_effort(items, date(2026, 9, 18)) is None

    def test_hard_session_log_counts_days_since(self):
        items = [_FakeLog(logged_date=date(2026, 9, 16), tss_actual=90, duration_minutes_actual=60)]
        assert days_since_hard_effort(items, date(2026, 9, 18)) == 2

    def test_hard_activity_counts_too(self):
        items = [_FakeActivity(activity_date=date(2026, 9, 17), tss=80, duration_seconds=3600)]
        assert days_since_hard_effort(items, date(2026, 9, 18)) == 1

    def test_picks_the_most_recent_hard_effort(self):
        items = [
            _FakeLog(logged_date=date(2026, 9, 10), tss_actual=90, duration_minutes_actual=60),
            _FakeLog(logged_date=date(2026, 9, 17), tss_actual=95, duration_minutes_actual=60),
        ]
        assert days_since_hard_effort(items, date(2026, 9, 18)) == 1

    def test_future_dated_item_is_ignored(self):
        items = [_FakeLog(logged_date=date(2026, 9, 20), tss_actual=95, duration_minutes_actual=60)]
        assert days_since_hard_effort(items, date(2026, 9, 18)) is None


class TestDaysSinceReturnFromBreak:
    def test_no_items_returns_none(self):
        assert days_since_return_from_break([], date(2026, 9, 21)) is None

    def test_no_gap_ever_reaches_the_threshold(self):
        items = [
            _FakeLog(logged_date=date(2026, 9, 19), tss_actual=50, duration_minutes_actual=60),
            _FakeLog(logged_date=date(2026, 9, 20), tss_actual=150, duration_minutes_actual=180),
        ]
        assert days_since_return_from_break(items, date(2026, 9, 21)) is None

    def test_still_mid_break_returns_zero(self):
        """No ride at all since the gap started — today would be the first day back."""
        items = [
            _FakeLog(
                logged_date=date(2026, 9, 21 - RETURN_FROM_BREAK_GAP_DAYS),
                tss_actual=100, duration_minutes_actual=90,
            )
        ]
        assert days_since_return_from_break(items, date(2026, 9, 21)) == 0

    def test_real_scenario_break_then_ramp_back(self):
        """Reported live 2026-09-21: last ride 2026-09-01, then 2026-09-12 (1h), a
        second smaller gap to 2026-09-19 (1h), 2026-09-20 (long Z2 ride), asking today
        2026-09-21. The break that matters is the 11-day one (09-01 -> 09-12) — the
        6-day gap after it never reaches the threshold on its own."""
        items = [
            _FakeLog(logged_date=date(2026, 9, 1), tss_actual=200, duration_minutes_actual=240),
            _FakeLog(logged_date=date(2026, 9, 12), tss_actual=40, duration_minutes_actual=60),
            _FakeLog(logged_date=date(2026, 9, 19), tss_actual=40, duration_minutes_actual=60),
            _FakeLog(logged_date=date(2026, 9, 20), tss_actual=120, duration_minutes_actual=170),
        ]
        assert days_since_return_from_break(items, date(2026, 9, 21)) == 9

    def test_outside_the_window_returns_none(self):
        """A break resolved long enough ago (and ridden through since) no longer
        matters — distinct from `test_still_mid_break_returns_zero`, where the gap is
        still open today."""
        today = date(2026, 9, 21)
        return_date = today - timedelta(days=RETURN_FROM_BREAK_WINDOW_DAYS + 1)
        break_start = return_date - timedelta(days=RETURN_FROM_BREAK_GAP_DAYS)
        items = [
            _FakeLog(logged_date=break_start, tss_actual=200, duration_minutes_actual=240),
            _FakeLog(logged_date=return_date, tss_actual=40, duration_minutes_actual=60),
            # Ridden regularly since, no individual gap reaching the break threshold again.
            _FakeLog(
                logged_date=return_date + timedelta(days=8),
                tss_actual=60, duration_minutes_actual=60,
            ),
            _FakeLog(
                logged_date=today - timedelta(days=1),
                tss_actual=60, duration_minutes_actual=60,
            ),
        ]
        assert days_since_return_from_break(items, today) is None

    def test_future_dated_item_is_ignored(self):
        items = [_FakeLog(logged_date=date(2026, 9, 22), tss_actual=50, duration_minutes_actual=60)]
        assert days_since_return_from_break(items, date(2026, 9, 21)) is None


class TestChooseWorkoutType:
    def test_surmenage_only_suggests_recovery(self):
        fitness = FitnessMetrics(atl=90, ctl=60, tsb=-40)
        choice = choose_workout_type(fitness, _SNAPSHOT, days_since_hard_effort=1)
        assert choice.workout_type == "recovery"

    def test_fatigue_normale_suggests_endurance(self):
        fitness = FitnessMetrics(atl=70, ctl=60, tsb=-15)
        choice = choose_workout_type(fitness, _SNAPSHOT, days_since_hard_effort=3)
        assert choice.workout_type == "endurance"

    def test_fresh_legs_and_good_form_suggests_intervals(self):
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(fitness, _SNAPSHOT, days_since_hard_effort=5)
        assert choice.workout_type == "intervals"

    def test_good_form_but_hard_effort_yesterday_avoids_another_hard_day(self):
        """US1 Acceptance Scenario 3: a hard effort yesterday must not be followed by
        another hard suggestion today, even though fitness alone would allow it."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        rested = choose_workout_type(fitness, _SNAPSHOT, days_since_hard_effort=5)
        tired = choose_workout_type(fitness, _SNAPSHOT, days_since_hard_effort=1)
        assert rested.workout_type == "intervals"
        assert tired.workout_type != "intervals"

    def test_respects_declared_preference_when_alternatives_exist(self):
        """US1 Acceptance Scenario 2: a disliked type is never proposed when another
        type would fit the current form just as well."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            avoid_workout_types=frozenset({"intervals"}),
        )
        assert choice.workout_type != "intervals"
        assert choice.preference_overridden is False

    def test_never_silently_ignores_an_impossible_avoid_list(self):
        """If every fitting type is avoided, the selector still answers (never
        refuses outright) but flags that the preference could not be honored —
        honesty over silence, consistent with Constitution Principle IV."""
        fitness = FitnessMetrics(atl=90, ctl=60, tsb=-40)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=1,
            avoid_workout_types=frozenset({"recovery", "endurance"}),
        )
        assert choice.preference_overridden is True
        assert choice.workout_type in {"recovery", "endurance"}

    def test_target_tss_scales_with_ctl(self):
        low = choose_workout_type(
            FitnessMetrics(atl=30, ctl=30, tsb=-15), _SNAPSHOT, days_since_hard_effort=3
        )
        high = choose_workout_type(
            FitnessMetrics(atl=30, ctl=90, tsb=-15), _SNAPSHOT, days_since_hard_effort=3
        )
        assert high.target_tss > low.target_tss

    def test_reasoning_summary_never_mentions_periodization(self):
        """SC-005: zero freestyle suggestions reference a phase or week number."""
        choice = choose_workout_type(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT, days_since_hard_effort=5
        )
        for forbidden in ("phase", "semaine", "week"):
            assert forbidden not in choice.reasoning_summary.lower()

    def test_requested_type_overrides_tsb_default(self):
        """spec 011 US1 Acceptance Scenario 1: an explicit request wins even against a
        fitness state that would normally suggest recovery."""
        fitness = FitnessMetrics(atl=90, ctl=60, tsb=-40)  # surmenage → would default to recovery
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=1, requested_workout_type="intervals",
        )
        assert choice.workout_type == "intervals"
        assert choice.target_tss > 0  # still computed from real fitness, never a stock value

    def test_requested_type_bypasses_standing_avoid_list(self):
        """spec 011 FR-009: an explicit, same-turn request overrides a standing dislike
        for that one suggestion — the standing preference mechanism itself is untouched."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            avoid_workout_types=frozenset({"intervals"}),
            requested_workout_type="intervals",
        )
        assert choice.workout_type == "intervals"
        assert choice.preference_overridden is False  # bypassed, not "every option exhausted"

    def test_default_conflicts_true_when_request_disagrees_with_default(self):
        """spec 011 US1 Acceptance Scenario 1: the coach must know it diverged, so it can
        say so honestly instead of presenting the request as the natural choice."""
        fitness = FitnessMetrics(atl=90, ctl=60, tsb=-40)  # would default to recovery
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=1, requested_workout_type="intervals",
        )
        assert choice.default_conflicts is True

    def test_default_conflicts_false_when_request_matches_default(self):
        """spec 011 US1 Acceptance Scenario 2: no conflict note when the request already
        matches what fitness alone would have suggested."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)  # would default to intervals
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, requested_workout_type="intervals",
        )
        assert choice.default_conflicts is False

    def test_default_conflicts_false_when_avoid_list_alone_shifts_the_pick(self):
        """FR-007 regression guard: an avoid-list-driven pick away from preferences[0]
        (spec 009 behavior, no request involved) must NOT be flagged as a request
        conflict — that would append a nonsensical note to a suggestion nobody asked to
        redirect."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)  # would default to intervals
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            avoid_workout_types=frozenset({"intervals"}),
        )
        assert choice.workout_type != "intervals"
        assert choice.default_conflicts is False

    def test_long_ride_target_tss_is_never_ctl_scaled(self):
        """Found live 2026-09-20: the old `_TSS_CTL_MULTIPLIER["long_ride"] = 1.4` guess
        gave a detrained athlete (low CTL) a ~65min "long ride" — a long ride is defined
        by duration, never by a fraction of CTL. A very low and a very high CTL must
        yield the exact same default duration/TSS when no explicit request is made."""
        low = choose_workout_type(
            FitnessMetrics(atl=10, ctl=25, tsb=20), _SNAPSHOT,
            days_since_hard_effort=None, requested_workout_type="long_ride",
            coaching_mode="power", ftp=220,
        )
        high = choose_workout_type(
            FitnessMetrics(atl=10, ctl=90, tsb=20), _SNAPSHOT,
            days_since_hard_effort=None, requested_workout_type="long_ride",
            coaching_mode="power", ftp=220,
        )
        assert low.target_tss == high.target_tss

    def test_long_ride_default_duration_meets_the_floor(self):
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=10, ctl=34, tsb=23), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=None,
            requested_workout_type="long_ride",
        )
        assert suggestion.duration_minutes == LONG_RIDE_MIN_MINUTES

    def test_long_ride_honors_an_explicit_duration_above_the_floor(self):
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=10, ctl=34, tsb=23), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=None,
            requested_workout_type="long_ride", available_minutes=180,
        )
        assert suggestion.duration_minutes == 180

    def test_return_from_break_caps_default_away_from_intervals(self):
        """Reported live 2026-09-21: a long-break-induced high TSB reads as freshness
        exactly like a real taper, but the athlete is detrained, not rested. The
        default pick must not default onto intervals during the reintroduction window,
        even though fitness alone would pick it (same fixture as the unguarded test
        `test_fresh_legs_and_good_form_suggests_intervals`)."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, days_since_return_from_break=9,
        )
        assert choice.workout_type != "intervals"
        assert "coupure" in choice.reasoning_summary.lower()

    def test_return_from_break_does_not_affect_non_intervals_default(self):
        """The caution is scoped to intervals only — endurance/long_ride/recovery
        defaults are untouched, matching the sourced guidance that endurance work is
        safe to resume immediately."""
        fitness = FitnessMetrics(atl=90, ctl=60, tsb=-40)  # would default to recovery anyway
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=1, days_since_return_from_break=9,
        )
        assert choice.workout_type == "recovery"
        assert "coupure" not in choice.reasoning_summary.lower()

    def test_explicit_intervals_request_honored_but_flagged_during_return_window(self):
        """An explicit ask still wins outright (never silently overridden, spec 011
        FR-009 precedent) — the caution becomes a spoken warning instead of a block."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            requested_workout_type="intervals", days_since_return_from_break=9,
        )
        assert choice.workout_type == "intervals"
        assert "coupure" in choice.reasoning_summary.lower()
        assert "progressivement" in choice.reasoning_summary.lower()

    def test_no_return_window_leaves_default_selection_unchanged(self):
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, days_since_return_from_break=None,
        )
        assert choice.workout_type == "intervals"
        assert "coupure" not in choice.reasoning_summary.lower()

    def test_acwr_caution_caps_default_away_from_intervals_only(self):
        """1.30-1.50 (relatively high, not yet Gabbett's danger zone) excludes
        `intervals` from the default pick but leaves `long_ride` available."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, acwr_finding_kind="acwr_caution",
        )
        assert choice.workout_type != "intervals"
        assert "sweet spot" in choice.reasoning_summary.lower()

    def test_acwr_high_caps_default_away_from_intervals_and_long_ride(self):
        """>1.50 = the actual danger zone — a long ride is, by definition, the week's
        biggest single load addition, which would defeat the guardrail's own
        "réduis la charge" action, so it's excluded too, not just intervals."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=20)  # would default to intervals
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            requested_workout_type=None, acwr_finding_kind="acwr_high",
        )
        assert choice.workout_type not in {"intervals", "long_ride"}
        assert "danger" in choice.reasoning_summary.lower()

    def test_acwr_high_explicit_request_honored_but_flagged(self):
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            requested_workout_type="long_ride", acwr_finding_kind="acwr_high",
        )
        assert choice.workout_type == "long_ride"
        assert "danger" in choice.reasoning_summary.lower()

    def test_no_acwr_finding_leaves_default_selection_unchanged(self):
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, acwr_finding_kind=None,
        )
        assert choice.workout_type == "intervals"
        assert "aiguë" not in choice.reasoning_summary.lower()

    def test_break_and_acwr_caps_combine_and_both_notes_appear(self):
        """The two safety caps are independent and can both apply at once — an
        over-eager ramp-back after a break is exactly how an athlete lands in both."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5,
            days_since_return_from_break=9, acwr_finding_kind="acwr_high",
        )
        assert choice.workout_type not in {"intervals", "long_ride"}
        assert "coupure" in choice.reasoning_summary.lower()
        assert "danger" in choice.reasoning_summary.lower()

    def test_unsupported_requested_type_is_ignored(self):
        """A value outside VALID_WORKOUT_TYPES (should never happen once the tool
        schema's enum constrains it, but defended here too) falls back to the
        fitness-driven default rather than crashing or being echoed back."""
        fitness = FitnessMetrics(atl=50, ctl=60, tsb=10)
        choice = choose_workout_type(
            fitness, _SNAPSHOT, days_since_hard_effort=5, requested_workout_type="yoga",
        )
        assert choice.workout_type == "intervals"
        assert choice.default_conflicts is False


class TestBuildFreestyleSuggestion:
    def test_returns_a_concrete_structured_session(self):
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10),
            _SNAPSHOT,
            coaching_mode="power",
            ftp=220,
            days_since_hard_effort=5,
        )
        assert suggestion.workout_type == "intervals"
        assert suggestion.duration_minutes > 0
        assert suggestion.target_tss > 0
        assert suggestion.steps

    def test_same_day_ordinal_is_reproducible(self):
        kwargs = dict(
            fitness=FitnessMetrics(atl=50, ctl=60, tsb=10),
            snapshot=_SNAPSHOT,
            coaching_mode="power",
            ftp=220,
            days_since_hard_effort=5,
            day_ordinal=42,
        )
        first = build_freestyle_suggestion(**kwargs)
        second = build_freestyle_suggestion(**kwargs)
        assert first == second

    def test_raises_when_no_template_covers_the_workout_type(self, monkeypatch):
        import app.engine.freestyle_selector as mod

        monkeypatch.setattr(mod, "candidates_for", lambda workout_type: (_ for _ in ()).throw(
            mod.SessionLibraryError("no coverage")
        ))
        with pytest.raises(mod.SessionLibraryError):
            build_freestyle_suggestion(
                FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
                coaching_mode="power", ftp=220, days_since_hard_effort=5,
            )

    def test_requested_type_conflict_note_appended_to_reasoning(self):
        """spec 011 Research Decision 6: the conflict note is appended, not a separate
        field — the athlete-facing narration is the single channel that carries it."""
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=90, ctl=60, tsb=-40),  # would default to recovery
            _SNAPSHOT, coaching_mode="power", ftp=220, days_since_hard_effort=1,
            requested_workout_type="intervals",
        )
        assert suggestion.workout_type == "intervals"
        assert "spontanément" in suggestion.reasoning_summary

    def test_no_conflict_note_when_request_matches_default(self):
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10),  # already defaults to intervals
            _SNAPSHOT, coaching_mode="power", ftp=220, days_since_hard_effort=5,
            requested_workout_type="intervals",
        )
        assert "spontanément" not in suggestion.reasoning_summary

    def test_requested_template_id_is_honored_when_it_fits(self):
        """spec 011 US2 Acceptance Scenario 1."""
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
            requested_template_id="vo2-5x5",
        )
        assert suggestion.template_id == "vo2-5x5"

    def test_requested_template_id_of_wrong_workout_type_is_ignored(self):
        """spec 011 US2 Acceptance Scenario 2 / FR-005: a template belonging to a
        different workout_type than the resolved one must never be forced through —
        falls back to the same rotation as if nothing had been requested."""
        default = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
        )
        with_mismatched_request = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
            requested_template_id="recovery-z1",  # workout_type=recovery, resolved type=intervals
        )
        assert with_mismatched_request.workout_type == "intervals"
        assert with_mismatched_request.template_id == default.template_id

    def test_requested_template_id_unknown_is_ignored(self):
        default = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
        )
        with_unknown_request = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
            requested_template_id="does-not-exist",
        )
        assert with_unknown_request.template_id == default.template_id

    def test_available_minutes_bounds_the_returned_duration(self):
        """spec 011 US3 Acceptance Scenario 1 — already-existing fit_template()
        behavior, exercised here through build_freestyle_suggestion()'s own parameter."""
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
            available_minutes=90,
        )
        assert suggestion.duration_minutes <= 90

    def test_requested_duration_is_honored_instead_of_treated_as_a_ceiling(self):
        suggestion = build_freestyle_suggestion(
            FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
            coaching_mode="power", ftp=220, days_since_hard_effort=5,
            requested_duration_minutes=90,
        )
        assert suggestion.duration_minutes == 90
        assert suggestion.duration_warning is not None

    def test_available_minutes_too_tight_raises_rather_than_exceeding(self):
        """spec 011 US3 Acceptance Scenario 2: nothing fits → say so, never overrun."""
        with pytest.raises(NoSuitableTemplateError):
            build_freestyle_suggestion(
                FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
                coaching_mode="power", ftp=220, days_since_hard_effort=5,
                available_minutes=5,
            )
