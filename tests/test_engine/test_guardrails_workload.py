"""Workload guardrail evaluators (spec 006 US1).

FR-022a: each threshold is tested including the cases where it must NOT fire.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.engine.guardrail_thresholds import (
    ACWR_SAFE_HIGH,
    MONOTONY_HIGH,
    RAMP_RATE_CAUTION,
    RAMP_RATE_HIGH,
)
from app.engine.guardrails import (
    GuardrailFinding,
    evaluate_acwr,
    evaluate_monotony,
    evaluate_ramp_rate,
)

D = date(2026, 8, 28)


# ── GuardrailFinding: an actionless finding is unconstructible (T013, FR-027, SC-003) ──


def test_finding_requires_a_non_empty_action():
    with pytest.raises(ValueError, match="action"):
        GuardrailFinding(
            kind="x", observed="1.4", reference="0.8–1.3", threshold="1.3",
            action="   ", severity=1, occurrence_key="x:2026-08-28",
        )


def test_finding_requires_observed_and_reference():
    with pytest.raises(ValueError):
        GuardrailFinding(
            kind="x", observed="", reference="0.8–1.3", threshold="1.3",
            action="do a thing", severity=1, occurrence_key="k",
        )


def test_a_valid_finding_constructs_and_is_frozen():
    f = GuardrailFinding(
        kind="acwr_high", observed="1.41", reference="0.80 – 1.30", threshold="1.30",
        action="réduis la charge", severity=3, occurrence_key="acwr_high:2026-08-28",
    )
    assert f.observed == "1.41"
    with pytest.raises(Exception):
        f.observed = "2.0"  # frozen


# ── acute:chronic ratio ──────────────────────────────────────────────────────


def test_acwr_inside_range_fires_nothing():
    assert evaluate_acwr(atl=100.0, ctl=100.0, finding_date=D) is None  # ratio 1.0
    assert evaluate_acwr(atl=120.0, ctl=100.0, finding_date=D) is None  # ratio 1.2


def test_acwr_below_range_is_not_raised_as_a_warning():
    """A taper produces a low ratio and must not be read as detraining (spec edge case)."""
    assert evaluate_acwr(atl=60.0, ctl=100.0, finding_date=D) is None  # ratio 0.6


def test_acwr_above_range_fires_with_a_load_reducing_action():
    f = evaluate_acwr(atl=141.0, ctl=100.0, finding_date=D)  # ratio 1.41
    assert f is not None
    assert f.kind == "acwr_high"
    assert f.observed == "1.41"
    assert str(ACWR_SAFE_HIGH) in f.threshold or f"{ACWR_SAFE_HIGH:.2f}" in f.threshold
    assert "réduis" in f.action.lower() or "réduire" in f.action.lower()


def test_acwr_none_when_ctl_below_minimum():
    """Thin chronic base — the ratio reflects the base, not a real spike."""
    assert evaluate_acwr(atl=40.0, ctl=10.0, finding_date=D) is None


def test_acwr_none_on_missing_data():
    assert evaluate_acwr(atl=None, ctl=100.0, finding_date=D) is None
    assert evaluate_acwr(atl=100.0, ctl=None, finding_date=D) is None
    assert evaluate_acwr(atl=100.0, ctl=0.0, finding_date=D) is None


# ── ramp rate ────────────────────────────────────────────────────────────────


def test_ramp_rate_below_caution_fires_nothing():
    assert evaluate_ramp_rate(RAMP_RATE_CAUTION - 1.0, finding_date=D) is None


def test_ramp_rate_in_caution_band_fires_medium():
    f = evaluate_ramp_rate(RAMP_RATE_CAUTION + 0.5, finding_date=D)
    assert f is not None and f.kind == "ramp_rate_caution"
    assert "n'ajoute pas" in f.action or "tiens le niveau" in f.action


def test_ramp_rate_above_high_fires_high():
    f = evaluate_ramp_rate(RAMP_RATE_HIGH + 1.0, finding_date=D)
    assert f is not None and f.kind == "ramp_rate_high"
    assert f.severity == 3


def test_ramp_rate_none_on_missing_data():
    assert evaluate_ramp_rate(None, finding_date=D) is None


# ── taper vs build: the two workload signals are independent (research R4) ─────


def test_taper_shape_fires_nothing():
    """Falling ratio + falling ramp — a deliberate taper, not a problem."""
    assert evaluate_acwr(atl=75.0, ctl=100.0, finding_date=D) is None
    assert evaluate_ramp_rate(1.5, finding_date=D) is None


def test_sustainable_build_fires_only_the_ratio_not_the_ramp():
    """Rising ratio, flat ramp — a hard week on a stable base."""
    assert evaluate_acwr(atl=140.0, ctl=100.0, finding_date=D) is not None
    assert evaluate_ramp_rate(3.0, finding_date=D) is None


def test_unsustainable_build_fires_both():
    assert evaluate_acwr(atl=140.0, ctl=100.0, finding_date=D) is not None
    assert evaluate_ramp_rate(RAMP_RATE_HIGH + 0.5, finding_date=D) is not None


# ── monotony ─────────────────────────────────────────────────────────────────


def test_monotony_below_threshold_fires_nothing():
    assert evaluate_monotony(MONOTONY_HIGH - 0.5, finding_date=D) is None
    assert evaluate_monotony(0.79, finding_date=D) is None  # the athlete's real week (R2)


def test_monotony_above_threshold_fires():
    f = evaluate_monotony(MONOTONY_HIGH + 1.0, finding_date=D)
    assert f is not None and f.kind == "monotony_high"
    assert f.action


def test_monotony_none_when_index_is_none():
    assert evaluate_monotony(None, finding_date=D) is None
