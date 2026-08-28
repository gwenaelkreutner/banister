"""Personal rolling baselines for recovery signals (spec 006 FR-006, FR-013, FR-015).

A baseline is the athlete's *own* recent normal for one signal — never a population
value (FR-006) — computed over a rolling window so it adapts as fitness changes
(FR-015). Below a minimum sample count it is `None`, never a default: an insufficient
baseline that returns a plausible number is the false-alarm source US5 exists to prevent
(FR-013, SC-004).

Pure function, no DB. `app/services/guardrail_service.py` supplies the dated values.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, stdev


def _in_window(
    values: list[tuple[date, float]], *, today: date, window_days: int
) -> list[float]:
    cutoff = today - timedelta(days=window_days)
    return [v for d, v in values if cutoff < d <= today]


def rolling_baseline(
    values: list[tuple[date, float]],
    *,
    today: date,
    window_days: int,
    min_samples: int,
) -> float | None:
    """Mean of the readings in `(today - window_days, today]`, or `None` if fewer than
    `min_samples` fall in that window.

    `values` may contain `None`-free (date, reading) pairs in any order; readings dated
    after `today` or before the window are ignored. A `None` reading is simply absent
    from the list the caller builds — this function never sees one, because a missing
    reading must not be counted as data (FR-014).
    """
    in_window = _in_window(values, today=today, window_days=window_days)
    if len(in_window) < min_samples:
        return None
    return mean(in_window)


def rolling_baseline_stats(
    values: list[tuple[date, float]],
    *,
    today: date,
    window_days: int,
    min_samples: int,
) -> tuple[float, float] | None:
    """`(mean, sample stdev)` over the window, or `None` below `min_samples`. The spread
    is what the outlier guard needs: a reading many standard deviations from the mean is
    a probable device error, not a real change (spec 006 FR-011)."""
    in_window = _in_window(values, today=today, window_days=window_days)
    if len(in_window) < min_samples:
        return None
    spread = stdev(in_window) if len(in_window) >= 2 else 0.0
    return mean(in_window), spread
