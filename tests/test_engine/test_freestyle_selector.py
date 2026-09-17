"""spec 009 US1 — freestyle session selection replaces periodization phase with
fitness state. No plan, no phase, ever referenced (SC-005); never guesses when
fitness is unavailable (FR-011, tested at the tool layer since this module always
receives an already-resolved FitnessMetrics).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from app.engine.atl_ctl import FitnessMetrics
from app.engine.freestyle_selector import (
    build_freestyle_suggestion,
    choose_workout_type,
    days_since_hard_effort,
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

        monkeypatch.setattr(mod, "_candidates_for", lambda workout_type: (_ for _ in ()).throw(
            mod.SessionLibraryError("no coverage")
        ))
        with pytest.raises(mod.SessionLibraryError):
            build_freestyle_suggestion(
                FitnessMetrics(atl=50, ctl=60, tsb=10), _SNAPSHOT,
                coaching_mode="power", ftp=220, days_since_hard_effort=5,
            )
