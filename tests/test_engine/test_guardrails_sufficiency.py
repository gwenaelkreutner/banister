"""Guardrails stay quiet when they cannot know (spec 006 US5).

Engine-level pieces: the baseline-sufficiency gate (T011) and the outlier / sustained
guard (T046-T047). The DB-level "baseline present, reading absent" scenario (T045) and
the insufficiency reason (T044/T048) are in tests/test_services/test_guardrail_service.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.engine.baselines import rolling_baseline, rolling_baseline_stats
from app.engine.guardrail_thresholds import (
    BASELINE_MIN_SAMPLES,
    BASELINE_WINDOW_DAYS,
    OUTLIER_SD,
)
from app.engine.guardrails import (
    evaluate_resting_hr,
    is_anomalous_reading,
    sustained_recovery_finding,
)

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


# ── outlier guard (T046, FR-011, SC-005) ─────────────────────────────────────


def test_is_anomalous_reading_flags_a_device_error_not_an_ordinary_change():
    # RHR baseline ~50: a real +7 is not a glitch; a jump to 110 is.
    assert is_anomalous_reading(57.0, 50.0, 3.0) is False
    assert is_anomalous_reading(110.0, 50.0, 3.0) is True


def test_is_anomalous_reading_has_a_relative_floor_on_a_flat_baseline():
    """A baseline with almost no spread must not make a normal fluctuation an outlier."""
    assert is_anomalous_reading(56.0, 50.0, 0.1) is False  # +6 bpm, floor keeps it in
    assert is_anomalous_reading(200.0, 50.0, 0.1) is True   # clearly a device error


def test_is_anomalous_reading_is_lenient_with_no_baseline_sd():
    assert is_anomalous_reading(99.0, 50.0, None) is False


def test_a_single_bad_day_does_not_fire():
    """One reading over threshold, nothing yesterday → no finding (FR-011)."""
    f = sustained_recovery_finding(
        evaluate_resting_hr,
        today_value=58.0,          # +8 over baseline 50
        yesterday_value=None,
        baseline_mean=50.0,
        baseline_sd=2.0,
        finding_date=TODAY,
    )
    assert f is None


def test_a_device_error_spike_does_not_fire_even_with_yesterday():
    """Today's value is a device-error outlier → not evaluated at all (SC-005)."""
    f = sustained_recovery_finding(
        evaluate_resting_hr,
        today_value=130.0,         # absurd for a resting HR — a sensor glitch
        yesterday_value=57.0,
        baseline_mean=50.0,
        baseline_sd=3.0,
        finding_date=TODAY,
    )
    assert f is None


def test_sustained_breach_over_two_days_fires():
    f = sustained_recovery_finding(
        evaluate_resting_hr,
        today_value=57.0,          # +7, within outlier bounds (50 ± 3*3)
        yesterday_value=56.0,      # +6, also breaching
        baseline_mean=50.0,
        baseline_sd=3.0,
        finding_date=TODAY,
    )
    assert f is not None and f.kind == "rhr_high"


def test_sustained_helper_returns_none_without_a_baseline():
    assert (
        sustained_recovery_finding(
            evaluate_resting_hr, 57.0, 56.0, None, None, finding_date=TODAY
        )
        is None
    )


def test_rolling_baseline_stats_returns_mean_and_spread():
    values = [(TODAY - timedelta(days=1 + i), 50.0 + (i % 4)) for i in range(BASELINE_MIN_SAMPLES)]
    stats = rolling_baseline_stats(
        values, today=TODAY, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )
    assert stats is not None
    mean_, sd_ = stats
    assert 50.0 <= mean_ <= 53.0 and sd_ > 0
    assert OUTLIER_SD == 3.0  # the guard uses this
