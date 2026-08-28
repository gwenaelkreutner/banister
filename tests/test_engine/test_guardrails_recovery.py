"""Recovery guardrail evaluators (spec 006 US2).

Fixtures, not the live account — research R1 found this athlete has no HRV, no sleep,
and no resting HR since 2026-07-19. FR-022a: each threshold is tested including the
cases where it must NOT fire.
"""
from __future__ import annotations

from datetime import date

from app.engine.guardrail_thresholds import HRV_DROP_PCT, RHR_RISE_BPM
from app.engine.guardrails import (
    SEVERITY_HIGH,
    combine_recovery_findings,
    evaluate_hrv,
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
