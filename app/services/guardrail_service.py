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
from app.engine.baselines import rolling_baseline_stats
from app.engine.guardrail_thresholds import BASELINE_MIN_SAMPLES, BASELINE_WINDOW_DAYS
from app.engine.guardrails import (
    RECOVERY_KINDS,
    GuardrailFinding,
    as_signal_only,
    combine_recovery_findings,
    evaluate_acwr,
    evaluate_hrv,
    evaluate_monotony,
    evaluate_ramp_rate,
    evaluate_resting_hr,
    state_conflict_with_plan,
    sustained_recovery_finding,
)
from app.engine.weekly_snapshot import compute_weekly_snapshot

# workout_type / zone codes that count as a "hard" prescribed session for FR-012.
_HARD_WORKOUT_TYPES = frozenset({"intervals"})
_HARD_ZONES = frozenset({"Z4", "Z5", "Z6"})


async def _apply_acknowledgements(
    session: AsyncSession, user_id: uuid.UUID, findings: list[GuardrailFinding]
) -> list[GuardrailFinding]:
    """A finding whose occurrence the athlete has declined keeps appearing (FR-026) but
    its action is demoted to a restatement (FR-025). An accepted occurrence is dropped —
    it has been acted on. No writes."""
    out: list[GuardrailFinding] = []
    for f in findings:
        ack = await repo.guardrail_repo.get_acknowledgement(
            session, user_id, f.occurrence_key
        )
        if ack is None:
            out.append(f)
        elif ack.decision == "declined":
            out.append(as_signal_only(f))
        # decision == "accepted" -> already acted on, do not re-raise
    return out


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

    real = await _apply_acknowledgements(
        session, user_id, [f for f in findings if f is not None]
    )
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
    yesterday = today - timedelta(days=1)

    hrv_series = [(w.date, w.hrv) for w in history if w.hrv is not None and w.date < today]
    rhr_series = [
        (w.date, float(w.resting_hr))
        for w in history
        if w.resting_hr is not None and w.date < today
    ]
    hrv_stats = rolling_baseline_stats(
        hrv_series, today=today, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )
    rhr_stats = rolling_baseline_stats(
        rhr_series, today=today, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
    )

    by_date = {w.date: w for w in history}
    today_row = await repo.wellness_repo.get_by_date(session, user_id, today)
    y_row = by_date.get(yesterday)

    def _hrv(row):
        return row.hrv if row is not None else None

    def _rhr(row):
        return float(row.resting_hr) if (row is not None and row.resting_hr is not None) else None

    hrv_mean, hrv_sd = hrv_stats if hrv_stats is not None else (None, None)
    rhr_mean, rhr_sd = rhr_stats if rhr_stats is not None else (None, None)

    raw = [
        sustained_recovery_finding(
            evaluate_hrv, _hrv(today_row), _hrv(y_row), hrv_mean, hrv_sd, finding_date=today
        ),
        sustained_recovery_finding(
            evaluate_resting_hr, _rhr(today_row), _rhr(y_row), rhr_mean, rhr_sd, finding_date=today
        ),
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

    findings = await _apply_acknowledgements(session, user_id, findings)
    findings.sort(key=lambda f: f.severity, reverse=True)
    return findings


async def recovery_insufficiency(
    session: AsyncSession, user_id: uuid.UUID, *, today: date | None = None
) -> str | None:
    """A one-line, athlete-facing reason the recovery guardrails cannot judge today —
    so the coach's context states it rather than being silently empty and letting the
    athlete assume recovery is fine (FR-013, FR-014, research R1).

    `None` when both signals are evaluable. Returns text when a signal has no baseline,
    or has a baseline but no reading today (the R1 state: a June/July RHR history and
    nothing since — the stale baseline must NOT be read as the current value).
    """
    today = today or date.today()
    # A long lookback here — not the 28-day baseline window — so the message can say
    # *when* a signal was last seen ("aucune mesure depuis le 19/07"), which is the R1
    # state and the whole point of FR-014.
    history = await repo.wellness_repo.get_range(
        session, user_id, today - timedelta(days=180), today
    )
    today_row = await repo.wellness_repo.get_by_date(session, user_id, today)

    def _status(field: str) -> str:
        readings = [(w.date, getattr(w, field)) for w in history if getattr(w, field) is not None]
        past = [(d, v) for d, v in readings if d < today]
        last = max((d for d, _ in past), default=None)
        stats = rolling_baseline_stats(
            [(d, float(v)) for d, v in past],
            today=today,
            window_days=BASELINE_WINDOW_DAYS,
            min_samples=BASELINE_MIN_SAMPLES,
        )
        has_today = today_row is not None and getattr(today_row, field) is not None

        if stats is not None and has_today:
            return ""  # fully evaluable
        if stats is not None:  # baseline current, no reading today
            return f"référence établie mais aucune mesure aujourd'hui (dernière : {last:%d/%m})"
        if last is None:
            return "aucun historique"
        if len(past) >= BASELINE_MIN_SAMPLES:  # was measured, then stopped (research R1)
            return f"référence établie par le passé mais plus aucune mesure depuis le {last:%d/%m}"
        return f"pas assez d'historique (dernière mesure : {last:%d/%m})"

    hrv_note = _status("hrv")
    rhr_note = _status("resting_hr")
    problems = []
    if hrv_note:
        problems.append(f"VFC : {hrv_note}")
    if rhr_note:
        problems.append(f"FC de repos : {rhr_note}")
    if not problems:
        return None
    return (
        "Je ne peux pas juger ta récupération aujourd'hui — "
        + " ; ".join(problems)
        + ". Ne dis pas que ta récupération est bonne : je n'en sais rien."
    )


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
