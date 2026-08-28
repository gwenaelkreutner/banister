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
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.engine.guardrails import (
    GuardrailFinding,
    evaluate_acwr,
    evaluate_monotony,
    evaluate_ramp_rate,
)
from app.engine.weekly_snapshot import compute_weekly_snapshot


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


def has_load_reduction_finding(findings: list[GuardrailFinding]) -> bool:
    """True when a finding present in the context requires load to come down — the coach
    must not recommend increasing load elsewhere in the same response (FR-003, SC-008)."""
    return any(f.kind in ("acwr_high", "ramp_rate_high") for f in findings)
