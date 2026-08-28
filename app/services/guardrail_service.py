"""Guardrail orchestration (spec 006): assemble the athlete's data, run the pure
evaluators in `app/engine/guardrails.py`, honour declined acknowledgements, and hand
`GuardrailFinding`s to the narration layer.

Provider-agnostic and aiogram-free (Constitution Principle III). This layer reads
`wellness` / `session_logs` / `activities`, calls the engine, and returns findings; it
performs **no** writes — a guardrail advises, it never mutates a plan or a calendar
(FR-023, SC-006). An accepted recommendation is dispatched to the *existing* approval
paths (spec 004 `plan_modifier`, spec 005 `authorize_publication`), which is what makes
SC-006 structural rather than tested.

Populated per user story: workload assembly (US1 = T018), recovery assembly (US2 = T025),
decline filtering (US4 = T042), insufficiency reasons (US5 = T048).
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.engine.baselines import rolling_baseline
from app.engine.guardrail_thresholds import BASELINE_MIN_SAMPLES, BASELINE_WINDOW_DAYS
from app.engine.guardrails import (
    RECOVERY_KINDS,
    GuardrailFinding,
    combine_recovery_findings,
    evaluate_acwr,
    evaluate_hrv,
    evaluate_monotony,
    evaluate_ramp_rate,
    evaluate_resting_hr,
    state_conflict_with_plan,
)
from app.engine.weekly_snapshot import compute_weekly_snapshot

# workout_type / zone codes that count as a "hard" prescribed session for FR-012.
_HARD_WORKOUT_TYPES = frozenset({"intervals"})
_HARD_ZONES = frozenset({"Z4", "Z5", "Z6"})


async def assemble_workload_findings(
    session: AsyncSession, user_id: uuid.UUID, *, today: date | None = None
) -> list[GuardrailFinding]:
    """Run the three workload evaluators against the athlete's current data and return
    the findings that fired, most severe first (US1).

    - acute:chronic ratio and ramp rate come from the latest `wellness` row — the
      source's own figures, consumed as-is (research R3, R4).
    - monotony comes from `compute_weekly_snapshot` over the athlete's logs + pre-plan
      activities (the corrected Foster value, research R2).

    No writes. `today` is injectable for tests; production passes `date.today()`.
    """
    today = today or date.today()

    latest = await repo.wellness_repo.get_latest(session, user_id, on_or_before=today)
    signal_date = latest.date if latest is not None else today

    findings: list[GuardrailFinding | None] = []
    if latest is not None:
        findings.append(
            evaluate_acwr(latest.atl, latest.ctl, finding_date=signal_date)
        )
        findings.append(
            evaluate_ramp_rate(latest.ramp_rate, finding_date=signal_date)
        )

    logs = await repo.session_log_repo.get_all_for_user(session, user_id)
    activities = await repo.activity_repo.get_for_user(session, user_id, days=90)
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    plan_start = plan.start_date if plan is not None else today
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]
    snapshot = compute_weekly_snapshot(list(logs) + pre_plan_acts, today)
    findings.append(
        evaluate_monotony(snapshot.monotony_index, finding_date=today)
    )

    real = [f for f in findings if f is not None]
    real.sort(key=lambda f: f.severity, reverse=True)
    return real


async def assemble_recovery_findings(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    prescribed_workout_type: str | None = None,
    prescribed_zone: str | None = None,
    today: date | None = None,
) -> list[GuardrailFinding]:
    """Evaluate today's HRV and resting HR against the athlete's own rolling baselines
    and return the findings that fired (US2).

    - Baselines come from `wellness` history over `BASELINE_WINDOW_DAYS`; below
      `BASELINE_MIN_SAMPLES` readings a baseline is `None` and nothing fires (FR-013).
    - Today's reading absent ⇒ `None` ⇒ nothing fires: a stale baseline is never reused
      as today's value (FR-014, research R1).
    - Two or more poor signals are combined into one higher-severity finding (FR-009).
    - When a finding lands on a day the plan prescribes a hard session, the conflict is
      stated openly in the finding's action (FR-012).

    No writes. `today` is injectable for tests.
    """
    today = today or date.today()
    window_start = today - timedelta(days=BASELINE_WINDOW_DAYS + 1)
    history = await repo.wellness_repo.get_range(session, user_id, window_start, today)

    hrv_series = [(w.date, w.hrv) for w in history if w.hrv is not None and w.date < today]
    rhr_series = [
        (w.date, float(w.resting_hr))
        for w in history
        if w.resting_hr is not None and w.date < today
    ]
    hrv_baseline = rolling_baseline(
        hrv_series, today=today, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )
    rhr_baseline = rolling_baseline(
        rhr_series, today=today, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )

    today_row = await repo.wellness_repo.get_by_date(session, user_id, today)
    hrv_today = today_row.hrv if today_row is not None else None
    rhr_today = (
        float(today_row.resting_hr)
        if (today_row is not None and today_row.resting_hr is not None)
        else None
    )

    raw = [
        evaluate_hrv(hrv_today, hrv_baseline, finding_date=today),
        evaluate_resting_hr(rhr_today, rhr_baseline, finding_date=today),
    ]
    findings = combine_recovery_findings([f for f in raw if f is not None])

    hard = (
        (prescribed_workout_type in _HARD_WORKOUT_TYPES)
        or (prescribed_zone in _HARD_ZONES)
    )
    if hard and findings:
        findings = [
            state_conflict_with_plan(
                f, prescribed_workout_type or "séance", prescribed_zone or ""
            )
            if f.kind in RECOVERY_KINDS
            else f
            for f in findings
        ]

    findings.sort(key=lambda f: f.severity, reverse=True)
    return findings


def has_load_reduction_finding(findings: list[GuardrailFinding]) -> bool:
    """True when a finding present in the context requires load to come down — the coach
    must not recommend increasing load elsewhere in the same response (FR-003, SC-008)."""
    return any(f.kind in ("acwr_high", "ramp_rate_high") for f in findings)


async def collect_registry_metrics(
    session: AsyncSession, user_id: uuid.UUID, *, today: date | None = None
) -> dict[str, float]:
    """The guardrail-derived metric values that are put in front of the model, keyed by
    the canonical names `response_verification` checks against (US3, FR-018). Ctl/atl/tsb
    come from `FitnessMetrics` at the call site; these are the wellness- and
    snapshot-derived ones.

    Only values actually shown to the model belong here — the registry is the definition
    of "retrieved".
    """
    today = today or date.today()
    out: dict[str, float] = {}

    latest = await repo.wellness_repo.get_latest(session, user_id, on_or_before=today)
    if latest is not None:
        if latest.ctl and latest.ctl > 0 and latest.atl is not None:
            out["acwr"] = round(latest.atl / latest.ctl, 2)
        if latest.ramp_rate is not None:
            out["ramp_rate"] = round(latest.ramp_rate, 1)

    logs = await repo.session_log_repo.get_all_for_user(session, user_id)
    activities = await repo.activity_repo.get_for_user(session, user_id, days=90)
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    plan_start = plan.start_date if plan is not None else today
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]
    snap = compute_weekly_snapshot(list(logs) + pre_plan_acts, today)
    if snap.monotony_index is not None:
        out["monotony"] = round(snap.monotony_index, 1)

    return out
