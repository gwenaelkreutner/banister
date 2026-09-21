"""Recovery guardrail evaluators (spec 006 US2).

Fixtures, not the live account — research R1 found this athlete has no HRV, no sleep,
and no resting HR since 2026-07-19. FR-022a: each threshold is tested including the
cases where it must NOT fire.
"""
from __future__ import annotations

from datetime import date

from app.engine.guardrail_thresholds import HRV_DROP_PCT, RECOVERY_INDEX_LOW, RHR_RISE_BPM
from app.engine.guardrails import (
    SEVERITY_HIGH,
    combine_recovery_findings,
    compute_recovery_index,
    evaluate_hrv,
    evaluate_recovery_index,
    evaluate_resting_hr,
    state_conflict_with_plan,
)

D = date(2026, 8, 28)


# ── HRV vs personal baseline (FR-007) ────────────────────────────────────────


def test_hrv_at_baseline_fires_nothing():
    assert evaluate_hrv(observed=60.0, baseline=60.0, finding_date=D) is None


def test_hrv_slightly_down_fires_nothing():
    # 10% below baseline — under the 20% threshold.
    assert evaluate_hrv(observed=54.0, baseline=60.0, finding_date=D) is None


def test_hrv_more_than_20pct_below_baseline_directs_an_easy_day():
    f = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)  # -25%
    assert f is not None
    assert f.kind == "hrv_low"
    assert "60" in f.reference           # baseline stated (FR-010)
    assert "45" in f.observed            # observed stated (FR-010)
    assert f"{HRV_DROP_PCT:.0f}%" in f.threshold
    assert "facile" in f.action or "repos" in f.action
    assert f.severity == SEVERITY_HIGH


def test_hrv_above_baseline_never_fires():
    assert evaluate_hrv(observed=75.0, baseline=60.0, finding_date=D) is None


def test_hrv_none_on_missing_observation_or_baseline():
    assert evaluate_hrv(observed=None, baseline=60.0, finding_date=D) is None
    assert evaluate_hrv(observed=45.0, baseline=None, finding_date=D) is None


# ── Resting HR vs personal baseline (FR-008) ─────────────────────────────────


def test_rhr_at_baseline_fires_nothing():
    assert evaluate_resting_hr(observed=50.0, baseline=50.0, finding_date=D) is None


def test_rhr_four_bpm_up_fires_nothing():
    assert evaluate_resting_hr(observed=54.0, baseline=50.0, finding_date=D) is None


def test_rhr_five_bpm_or_more_above_baseline_raises_a_fatigue_signal():
    f = evaluate_resting_hr(observed=55.0, baseline=50.0, finding_date=D)
    assert f is not None
    assert f.kind == "rhr_high"
    assert "50" in f.reference
    assert "55" in f.observed
    assert f"+{RHR_RISE_BPM}" in f.threshold
    assert "fatigue" in f.action.lower() or "infection" in f.action.lower()


def test_rhr_none_on_missing_data():
    assert evaluate_resting_hr(observed=None, baseline=50.0, finding_date=D) is None
    assert evaluate_resting_hr(observed=55.0, baseline=None, finding_date=D) is None


# ── Multiple poor signals compound (FR-009) ──────────────────────────────────


def test_two_recovery_findings_become_one_of_higher_significance():
    hrv = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)
    rhr = evaluate_resting_hr(observed=56.0, baseline=50.0, finding_date=D)
    combined = combine_recovery_findings([hrv, rhr])

    assert len(combined) == 1
    only = combined[0]
    assert only.kind == "recovery_multi"
    assert only.severity == SEVERITY_HIGH
    # Both underlying observations survive in the combined statement.
    assert "45" in only.observed and "56" in only.observed
    assert "plus significatif" in only.action or "simultanément" in only.action


def test_one_recovery_finding_passes_through_unchanged():
    hrv = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)
    assert combine_recovery_findings([hrv]) == [hrv]


def test_combine_leaves_non_recovery_findings_alone():
    from app.engine.guardrails import GuardrailFinding

    workload = GuardrailFinding(
        kind="acwr_high", observed="1.4", reference="0.8–1.3", threshold="1.3",
        action="reduce", severity=3, occurrence_key="acwr_high:2026-08-28",
    )
    hrv = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)
    rhr = evaluate_resting_hr(observed=56.0, baseline=50.0, finding_date=D)
    out = combine_recovery_findings([workload, hrv, rhr])
    assert workload in out
    assert any(f.kind == "recovery_multi" for f in out)
    assert len(out) == 2


def test_all_normal_fires_nothing():
    assert evaluate_hrv(observed=61.0, baseline=60.0, finding_date=D) is None
    assert evaluate_resting_hr(observed=50.0, baseline=50.0, finding_date=D) is None


# ── Conflict with a hard prescribed session is stated openly (T022, FR-012) ───


def test_recovery_finding_against_a_hard_session_names_the_conflict():
    hrv = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)
    stated = state_conflict_with_plan(hrv, "intervals", "Z4")

    assert stated.kind == hrv.kind
    assert "plan prévoit" in stated.action
    assert "intervals" in stated.action and "Z4" in stated.action
    # It surfaces the conflict — it does not silently resolve it.
    assert "propose l'échange" in stated.action or "ne tranche pas" in stated.action
    # The original recovery guidance is still there.
    assert "facile" in stated.action or "repos" in stated.action


# ── recovery_index — composite ratio (2026-09-21, Section11-inspired) ────────


def test_compute_recovery_index_at_baseline_equals_one():
    assert compute_recovery_index(60.0, 60.0, 50.0, 50.0) == 1.0


def test_compute_recovery_index_hrv_low_and_rhr_high_both_lower_the_ratio():
    # HRV 10% below baseline AND RHR 10% above baseline compound.
    idx = compute_recovery_index(54.0, 60.0, 55.0, 50.0)
    assert idx == (54.0 / 60.0) / (55.0 / 50.0)
    assert idx < 1.0


def test_compute_recovery_index_none_on_any_missing_input():
    assert compute_recovery_index(None, 60.0, 50.0, 50.0) is None
    assert compute_recovery_index(60.0, None, 50.0, 50.0) is None
    assert compute_recovery_index(60.0, 60.0, None, 50.0) is None
    assert compute_recovery_index(60.0, 60.0, 50.0, None) is None


def test_compute_recovery_index_none_on_zero_baseline():
    assert compute_recovery_index(60.0, 0.0, 50.0, 50.0) is None
    assert compute_recovery_index(60.0, 60.0, 50.0, 0.0) is None


def test_evaluate_recovery_index_at_or_above_threshold_fires_nothing():
    assert evaluate_recovery_index(RECOVERY_INDEX_LOW, finding_date=D) is None
    assert evaluate_recovery_index(1.0, finding_date=D) is None


def test_evaluate_recovery_index_below_threshold_fires_same_day():
    """No 2-consecutive-day requirement here (unlike evaluate_hrv/evaluate_resting_hr) —
    the ratio is already built on two 7d-smoothed baselines, not a raw single reading."""
    f = evaluate_recovery_index(0.75, finding_date=D)
    assert f is not None
    assert f.kind == "recovery_index_low"
    assert "0.75" in f.observed
    assert f"{RECOVERY_INDEX_LOW:.2f}" in f.reference
    assert "facile" in f.action.lower()


def test_evaluate_recovery_index_none_on_missing_value():
    assert evaluate_recovery_index(None, finding_date=D) is None


def test_recovery_index_low_participates_in_combine_recovery_findings():
    hrv = evaluate_hrv(observed=45.0, baseline=60.0, finding_date=D)
    idx = evaluate_recovery_index(0.75, finding_date=D)
    combined = combine_recovery_findings([hrv, idx])
    assert len(combined) == 1
    assert combined[0].kind == "recovery_multi"
