"""Sport context for other local services (internal API, `app/api/internal.py`).

One compact, dated read model of what Banister knows about the athlete's cycling: current
form, recovery signals, recent rides and what is planned next. Built for the Hermes
mobility coach (`/root/mobilite`) so it never has to read Banister's SQLite directly.

Read-only and deterministic: every figure comes from an existing path — `get_current_fitness()`
for CTL/ATL/TSB, the spec 006 guardrail assembly for load/recovery findings,
`iter_horizon_sessions()` for planned dates — nothing is recomputed or narrated here.
A value the source never measured stays `None`; it is never estimated.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import paris_today
from app.db import repositories as repo
from app.engine.guardrails import GuardrailFinding
from app.engine.schemas import TrainingPlanSchema
from app.providers.intervals.calendar import iter_horizon_sessions
from app.services.coaching_mode import mode_from_plan
from app.services.fitness import get_current_fitness
from app.services.guardrail_service import (
    assemble_recovery_findings,
    assemble_workload_findings,
    recovery_insufficiency,
)

MAX_DAYS = 28
MAX_AHEAD = 14


def _r(value, digits: int = 1):
    return round(value, digits) if isinstance(value, (int, float)) else value


def _mean(values) -> float | None:
    values = [v for v in values if isinstance(v, (int, float))]
    return round(sum(values) / len(values), 1) if values else None


def _finding(f: GuardrailFinding) -> dict:
    return {
        "kind": f.kind,
        "observed": f.observed,
        "reference": f.reference,
        "threshold": f.threshold,
        "action": f.action,
        "severity": f.severity,
    }


async def _recovery(session: AsyncSession, user_id: uuid.UUID, today: date) -> dict:
    """Latest wellness reading (dated — may be older than today) with 7-day averages of
    the days before it, so the reader can compare without a second call."""
    rows = await repo.wellness_repo.get_range(session, user_id, today - timedelta(days=8), today)
    if not rows:
        return {"available": False}
    latest, previous = rows[-1], rows[:-1][-7:]
    return {
        "available": True,
        "date": latest.date.isoformat(),
        "hrv": latest.hrv,
        "hrv_avg_7d": _mean(r.hrv for r in previous),
        "resting_hr": latest.resting_hr,
        "resting_hr_avg_7d": _mean(r.resting_hr for r in previous),
        "sleep_hours": _r(latest.sleep_seconds / 3600) if latest.sleep_seconds else None,
        "sleep_score": latest.sleep_score,
        # intervals.icu 1-4 scales, 1 = best state
        "fatigue": latest.fatigue,
        "soreness": latest.soreness,
        "stress": latest.stress,
        "mood": latest.mood,
        "motivation": latest.motivation,
        "readiness": latest.readiness,
    }


def _planned_from_plan(plan, today: date, end: date) -> list[dict]:
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    return [
        {
            "date": p.session_date.isoformat(),
            "workout_type": p.spec.workout_type,
            "zone": p.spec.zone_code,
            "duration_min": p.spec.duration_minutes,
            "tss_target": _r(p.spec.tss_target, 0),
            "description": p.spec.description_fr,
            "source": "plan",
        }
        for p in iter_horizon_sessions(schema, today, end, today=today)
    ]


def _profile(profile_row) -> dict:
    p = (profile_row.profile if profile_row else None) or {}
    return {
        "level": p.get("level"),
        "objective": (p.get("objective") or {}).get("type"),
        "hours_per_week": (p.get("availability") or {}).get("hours_per_week"),
        "health_constraints": p.get("health_constraints"),
        "injury_status": p.get("injury_status"),
    }


async def build_sport_context(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    today: date | None = None,
    days: int = 7,
    ahead: int = 7,
) -> dict:
    today = today or paris_today()
    days = max(1, min(days, MAX_DAYS))
    ahead = max(1, min(ahead, MAX_AHEAD))

    fitness = await get_current_fitness(session, user_id, today=today)
    latest = await repo.wellness_repo.get_latest(session, user_id, on_or_before=today)

    logs = await repo.session_log_repo.get_in_range(
        session, user_id, today - timedelta(days=days - 1), today
    )
    recent = [
        {
            "date": log.logged_date.isoformat(),
            "type": log.session_type_real,
            "duration_min": log.duration_minutes_actual,
            "tss": _r(log.tss_actual, 0),
            "rpe": log.rpe,
            "planned": log.status == "done",
        }
        for log in logs
    ]

    plan = await repo.plan_repo.get_active_plan(session, user_id)
    end = today + timedelta(days=ahead - 1)
    planned = _planned_from_plan(plan, today, end) if plan else []
    for entry in await repo.freestyle_publication_repo.get_active_for_user(session, user_id):
        if today <= entry.session_date <= end:
            planned.append({
                "date": entry.session_date.isoformat(),
                "workout_type": entry.workout_type,
                "source": "freestyle",
            })
    planned.sort(key=lambda s: s["date"])

    today_session = next((s for s in planned if s["date"] == today.isoformat()), {})
    findings = await assemble_workload_findings(session, user_id, today=today)
    findings += await assemble_recovery_findings(
        session,
        user_id,
        prescribed_workout_type=today_session.get("workout_type"),
        prescribed_zone=today_session.get("zone"),
        today=today,
    )
    findings.sort(key=lambda f: f.severity, reverse=True)

    return {
        "date": today.isoformat(),
        "coaching_mode": mode_from_plan(plan),
        "fitness": {
            "date": fitness.as_of.isoformat(),
            "stale": fitness.is_stale,
            "ctl": _r(fitness.metrics.ctl),
            "atl": _r(fitness.metrics.atl),
            "tsb": fitness.metrics.tsb,
            "ramp_rate": _r(latest.ramp_rate) if latest else None,
        } if fitness else None,
        "recovery": await _recovery(session, user_id, today),
        "recovery_unknown_reason": await recovery_insufficiency(session, user_id, today=today),
        "guardrails": [_finding(f) for f in findings],
        "recent_rides": recent,
        "recent_days": days,
        "recent_tss": _r(sum(r["tss"] or 0 for r in recent), 0),
        "planned": planned,
        "profile": _profile(await repo.profile_repo.get_by_user_id(session, user_id)),
    }
