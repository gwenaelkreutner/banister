"""Workload-finding assembly (spec 006 US1 = T018).

Ties the pure evaluators to the athlete's stored data. The no-writes / authority
guarantees (FR-023, SC-006) are exercised in Phase 6's test_guardrail_authority.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.models.user import User
from app.db.repositories import wellness_repo
from app.engine.guardrail_thresholds import BASELINE_MIN_SAMPLES
from app.services.guardrail_service import (
    assemble_recovery_findings,
    assemble_workload_findings,
    collect_registry_metrics,
    has_load_reduction_finding,
)

TODAY = date(2026, 8, 28)


async def _user(session) -> User:
    u = User(telegram_id=1, first_name="Test")
    session.add(u)
    await session.flush()
    return u


async def test_no_wellness_no_findings(db_session):
    u = await _user(db_session)
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []


async def test_high_ratio_and_ramp_produce_findings_most_severe_first(db_session):
    u = await _user(db_session)
    # The athlete's real 2026-08-25 shape: ATL/CTL ≈ 1.44, ramp 6.43. 1.44 sits in the
    # literature's "relatively high" caution band (1.30–1.50), not yet the Gabbett
    # danger zone (>1.50) — see `evaluate_acwr()`'s two-tier banding (2026-09-21).
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=44.0, atl=63.5, ramp_rate=6.43
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)

    kinds = [f.kind for f in findings]
    assert "acwr_caution" in kinds
    assert "ramp_rate_caution" in kinds  # 6.43 is in the 5–7 caution band
    assert findings == sorted(findings, key=lambda f: f.severity, reverse=True)
    assert not has_load_reduction_finding(findings)  # caution-only, not the danger zone
    assert all(f.action for f in findings)  # SC-003


async def test_ratio_above_the_danger_zone_requires_load_down(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(db_session, u.id, TODAY, ctl=100.0, atl=170.0)  # ratio 1.70
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)

    kinds = [f.kind for f in findings]
    assert "acwr_high" in kinds
    assert has_load_reduction_finding(findings)


async def test_normal_load_produces_no_manufactured_warning(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=50.0, atl=52.0, ramp_rate=2.0
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []  # FR-005


async def test_uses_latest_wellness_row_not_a_stale_one(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(
        db_session, u.id, TODAY - timedelta(days=10), ctl=40.0, atl=80.0, ramp_rate=9.0
    )
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=50.0, atl=50.0, ramp_rate=1.0
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []  # today's row is calm; the 10-day-old spike is not used


# ── recovery assembly ────────────────────────────────────────────────────────


async def _seed_rhr_baseline(session, user_id, *, value: float, n: int = BASELINE_MIN_SAMPLES):
    for i in range(n):
        await wellness_repo.upsert(
            session, user_id, TODAY - timedelta(days=2 + i), resting_hr=int(value)
        )


async def test_recovery_no_history_fires_nothing(db_session):
    u = await _user(db_session)
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert findings == []  # FR-013


async def test_recovery_baseline_present_but_today_absent_fires_nothing(db_session):
    """research R1 — a complete baseline and no reading since. The stale baseline must
    NOT be reused as today's value (FR-014)."""
    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    # No row for TODAY at all.
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert findings == []


async def test_recovery_rhr_elevated_for_two_days_fires(db_session):
    """FR-011 — a sustained breach fires; a single day would not (see the sufficiency
    engine tests)."""
    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY - timedelta(days=1), resting_hr=56)  # +6
    await wellness_repo.upsert(db_session, u.id, TODAY, resting_hr=57)                       # +7
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert [f.kind for f in findings] == ["rhr_high"]
    assert findings[0].action


async def test_recovery_single_elevated_day_does_not_fire(db_session):
    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY, resting_hr=57)  # today only
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert findings == []  # FR-011 — one reading never fires alone


async def test_recovery_conflict_with_a_hard_prescribed_session_is_stated(db_session):
    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY - timedelta(days=1), resting_hr=57)
    await wellness_repo.upsert(db_session, u.id, TODAY, resting_hr=58)
    findings = await assemble_recovery_findings(
        db_session, u.id, prescribed_workout_type="intervals", prescribed_zone="Z4", today=TODAY
    )
    assert findings and "plan prévoit" in findings[0].action  # FR-012


async def test_recovery_insufficiency_reason_when_baseline_present_but_reading_absent(db_session):
    """FR-013/FR-014 — a current baseline, nothing today: the coach's context must say
    it cannot judge, not stay silent."""
    from app.services.guardrail_service import recovery_insufficiency

    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)  # full recent baseline, nothing today

    note = await recovery_insufficiency(db_session, u.id, today=TODAY)
    assert note is not None
    assert "FC de repos" in note
    assert "aucune mesure aujourd'hui" in note
    assert "n'en sais rien" in note  # never implies recovery is fine


async def test_recovery_insufficiency_names_a_baseline_gone_stale(db_session):
    """research R1's exact state — measured for weeks, then the device stopped syncing.
    The stale baseline must NOT be treated as current (FR-014)."""
    from app.services.guardrail_service import recovery_insufficiency

    u = await _user(db_session)
    for i in range(20):  # 20 readings, all 40+ days ago
        await wellness_repo.upsert(
            db_session, u.id, TODAY - timedelta(days=45 + i), resting_hr=50
        )
    note = await recovery_insufficiency(db_session, u.id, today=TODAY)
    assert note is not None
    assert "plus aucune mesure depuis" in note


async def test_recovery_insufficiency_none_when_fully_evaluable(db_session):
    from app.services.guardrail_service import recovery_insufficiency

    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY, resting_hr=50)
    # HRV still has no data — so a note is still expected, mentioning VFC only.
    note = await recovery_insufficiency(db_session, u.id, today=TODAY)
    assert note is not None and "VFC" in note and "FC de repos" not in note


# ── recovery_index (2026-09-21, Section11-inspired) ──────────────────────────


async def _seed_hrv_and_rhr_baseline(
    session, user_id, *, hrv: float, rhr: float, n: int = 7
):
    for i in range(n):
        await wellness_repo.upsert(
            session, user_id, TODAY - timedelta(days=2 + i), hrv=hrv, resting_hr=int(rhr)
        )


async def test_recovery_index_low_fires_when_hrv_and_rhr_both_diverge(db_session):
    u = await _user(db_session)
    await _seed_hrv_and_rhr_baseline(db_session, u.id, hrv=60.0, rhr=50.0)
    # HRV down 15%, RHR up 10% vs the 7d baseline — neither alone crosses hrv_low/rhr_high
    # (needs -20%/+5bpm sustained 2 days), but the composite ratio does.
    await wellness_repo.upsert(db_session, u.id, TODAY, hrv=51.0, resting_hr=55)
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert [f.kind for f in findings] == ["recovery_index_low"]


async def test_recovery_index_stays_silent_when_hrv_and_rhr_are_normal(db_session):
    u = await _user(db_session)
    await _seed_hrv_and_rhr_baseline(db_session, u.id, hrv=60.0, rhr=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY, hrv=60.0, resting_hr=50)
    findings = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert findings == []


async def test_collect_registry_metrics_includes_recovery_index_hrv_rhr(db_session):
    u = await _user(db_session)
    await _seed_hrv_and_rhr_baseline(db_session, u.id, hrv=60.0, rhr=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY, hrv=51.0, resting_hr=55)
    metrics = await collect_registry_metrics(db_session, u.id, today=TODAY)
    assert metrics["hrv"] == 51.0
    assert metrics["rhr"] == 55.0
    assert metrics["recovery_index"] == round((51.0 / 60.0) / (55.0 / 50.0), 2)


async def test_collect_registry_metrics_omits_recovery_index_without_enough_history(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(db_session, u.id, TODAY, hrv=51.0, resting_hr=55)
    metrics = await collect_registry_metrics(db_session, u.id, today=TODAY)
    assert metrics["hrv"] == 51.0  # today's raw reading is still registered
    assert "recovery_index" not in metrics  # no 7d baseline yet


# ── Freestyle mode regression pin (spec 009 research Decision 7) ────────────────
# assemble_workload_findings/assemble_recovery_findings were already plan-agnostic
# before spec 009 (both fall back to `plan_start = today` when there is no active
# plan) — this pins that claim so a future change can't silently reintroduce a
# dependency on an active plan existing.


async def test_workload_findings_identical_with_or_without_an_active_plan(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(db_session, u.id, TODAY, ctl=44.0, atl=63.5, ramp_rate=6.43)

    without_plan = await assemble_workload_findings(db_session, u.id, today=TODAY)

    from app.db.repositories import plan_repo

    await plan_repo.create(
        db_session, u.id, plan_technical={}, start_date=TODAY - timedelta(days=30),
        end_date=TODAY + timedelta(days=30),
    )
    with_plan = await assemble_workload_findings(db_session, u.id, today=TODAY)

    assert [f.kind for f in without_plan] == [f.kind for f in with_plan]
    assert [f.severity for f in without_plan] == [f.severity for f in with_plan]


async def test_recovery_findings_identical_with_or_without_an_active_plan(db_session):
    u = await _user(db_session)
    await _seed_rhr_baseline(db_session, u.id, value=50.0)
    await wellness_repo.upsert(db_session, u.id, TODAY, resting_hr=65)  # elevated, day 1

    without_plan = await assemble_recovery_findings(db_session, u.id, today=TODAY)

    from app.db.repositories import plan_repo

    await plan_repo.create(
        db_session, u.id, plan_technical={}, start_date=TODAY - timedelta(days=30),
        end_date=TODAY + timedelta(days=30),
    )
    with_plan = await assemble_recovery_findings(db_session, u.id, today=TODAY)

    assert [f.kind for f in without_plan] == [f.kind for f in with_plan]
