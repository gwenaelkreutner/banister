"""Guardrails stay quiet when they cannot know (spec 006 US5).

Phase 2 seeds this file with the baseline-sufficiency tests (T011). The rest of US5 —
the "baseline present, observation absent" case, the outlier guard, activation on new
history — lands in Phase 7 (T044-T046).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.engine.baselines import rolling_baseline
from app.engine.guardrail_thresholds import BASELINE_MIN_SAMPLES, BASELINE_WINDOW_DAYS

TODAY = date(2026, 8, 28)


def _series(n: int, *, value: float = 50.0, start_offset: int = 1) -> list[tuple[date, float]]:
    """`n` daily readings ending `start_offset` days before TODAY."""
    return [(TODAY - timedelta(days=start_offset + i), value) for i in range(n)]


def test_baseline_is_none_below_minimum_samples():
    values = _series(BASELINE_MIN_SAMPLES - 1)
    assert (
        rolling_baseline(
            values,
            today=TODAY,
            window_days=BASELINE_WINDOW_DAYS,
            min_samples=BASELINE_MIN_SAMPLES,
        )
        is None
    )


def test_baseline_is_the_mean_once_minimum_is_reached():
    values = _series(BASELINE_MIN_SAMPLES, value=48.0)
    result = rolling_baseline(
        values,
        today=TODAY,
        window_days=BASELINE_WINDOW_DAYS,
        min_samples=BASELINE_MIN_SAMPLES,
    )
    assert result == 48.0


def test_baseline_reflects_only_the_supplied_history_not_a_population_value():
    # An unusual personal normal (a low-HRV athlete) must be honoured, not "corrected".
    values = _series(20, value=22.0)
    result = rolling_baseline(
        values, today=TODAY, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )
    assert result == 22.0


def test_readings_outside_the_window_are_ignored():
    recent = _series(BASELINE_MIN_SAMPLES, value=50.0, start_offset=1)
    stale = _series(30, value=999.0, start_offset=BASELINE_WINDOW_DAYS + 5)
    result = rolling_baseline(
        recent + stale,
        today=TODAY,
        window_days=BASELINE_WINDOW_DAYS,
        min_samples=BASELINE_MIN_SAMPLES,
    )
    assert result == 50.0  # the stale 999s never enter the mean


def test_baseline_activates_when_history_crosses_the_minimum_with_no_other_change():
    """US5 acceptance 4 — history becoming sufficient activates the signal on its own."""
    below = _series(BASELINE_MIN_SAMPLES - 1)
    assert (
        rolling_baseline(
            below, today=TODAY, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
        )
        is None
    )
    at = _series(BASELINE_MIN_SAMPLES)
    assert (
        rolling_baseline(
            at, today=TODAY, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
        )
        is not None
    )


def test_future_dated_readings_are_ignored():
    values = _series(BASELINE_MIN_SAMPLES) + [(TODAY + timedelta(days=2), 999.0)]
    result = rolling_baseline(
        values, today=TODAY, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )
    assert result == 50.0
