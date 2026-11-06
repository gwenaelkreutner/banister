"""Check an external agent's final text against what it actually retrieved.

The same guarantee as the built-in chat (Constitution Principle I, spec 006 US3), for a reply
written outside Banister: current metrics are read here, and the values the agent received
from its tools are registered from `retrieved`. Bad sentences are withheld and recorded.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.user import User
from app.services.fitness import get_current_fitness
from app.services.guardrail_service import collect_registry_metrics
from app.services.response_verification import (
    MetricRegistry,
    apply_result,
    verify_response,
)


def _dated(
    registry: MetricRegistry, metric: str, items: list, date_key: str, value_key: str
) -> None:
    registry.register_history(
        metric, {i[date_key]: i.get(value_key) for i in items if i.get(date_key)}
    )


def register_retrieved(registry: MetricRegistry, kind: str, result: dict) -> None:
    """`kind` is the agent API resource the result came from."""
    if not isinstance(result, dict):
        return
    if kind == "fitness":
        for metric in ("ctl", "atl", "tsb", "ramp_rate"):
            _dated(registry, metric, result.get("points", []), "date", metric)
    elif kind == "trend":
        _dated(registry, "tss", result.get("weeks", []), "week_start", "tss")
    elif kind == "wellness":
        _dated(registry, "hrv", result.get("points", []), "date", "hrv")
        _dated(registry, "rhr", result.get("points", []), "date", "resting_hr")
    elif kind == "ride":
        as_of = result.get("date", "")
        _dated(
            registry,
            "tss",
            [{"date": as_of, "tss": s.get("tss")} for s in result.get("sessions", [])],
            "date",
            "tss",
        )
    elif kind == "context":
        _dated(registry, "tss", result.get("recent_rides", []), "date", "tss")
        registry.register_also("tss", result.get("recent_tss"))
        for planned in result.get("planned", []):
            registry.register_also("tss", planned.get("tss_target"))
        recovery = result.get("recovery") or {}
        registry.register_also("hrv", recovery.get("hrv"))
        registry.register_also("hrv", recovery.get("hrv_avg_7d"))
        registry.register_also("rhr", recovery.get("resting_hr"))
        registry.register_also("rhr", recovery.get("resting_hr_avg_7d"))
    elif kind == "session":
        registry.register_also("tss", result.get("tss_estimate"))
        reference = result.get("engine_reference") or {}
        registry.register_also("tss", reference.get("target_tss"))
        registry.register_also("tss", (reference.get("recent_peak_day") or {}).get("tss"))
    elif kind == "recommendation":
        registry.register_also("tss", result.get("target_tss"))
    elif kind == "recap":
        registry.register_also("tss", result.get("tss_7d"))
    elif kind == "plan_proposal":
        registry.register_also("tss", result.get("first_week_tss"))
        registry.register_also("tss", result.get("peak_weekly_tss"))
    elif kind == "plan":
        plan = result.get("plan") or {}
        week = result.get("week") or {}
        registry.register_also("tss", plan.get("peak_weekly_tss"))
        registry.register_also("tss", week.get("total_tss_target"))
        for item in plan.get("weeks") or []:
            registry.register_also("tss", item.get("total_tss_target"))
        for item in (week.get("sessions_by_date") or {}).values():
            registry.register_also("tss", item.get("tss_target"))


async def verify_agent_reply(
    session: AsyncSession, user: User, text: str, retrieved: list[dict]
) -> dict:
    """`{"ok": bool, "text": str}`: `text` is the reply to send, bad sentences replaced."""
    registry = MetricRegistry()
    current = await get_current_fitness(session, user.id)
    if current is not None:
        registry.register("ctl", current.metrics.ctl)
        registry.register("atl", current.metrics.atl)
        registry.register("tsb", current.metrics.tsb)
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    ftp = ((profile_row.profile or {}).get("equipment") or {}).get("ftp") if profile_row else None
    registry.register("ftp", ftp)
    for name, value in (await collect_registry_metrics(session, user.id)).items():
        registry.register(name, value)
    for item in retrieved:
        register_retrieved(registry, item.get("kind", ""), item.get("result") or {})

    result = verify_response(text, registry)
    if result.ok:
        return {"ok": True, "text": text}
    for claim, expected in result.mismatches:
        await repo.guardrail_repo.record_check_failure(
            session,
            user_id=user.id,
            failure_kind="mismatch",
            metric_name=claim.metric,
            stated_value=claim.stated_text,
            expected_value=f"{expected:g}",
            response_excerpt=claim.sentence,
        )
    for claim in result.unretrieved:
        await repo.guardrail_repo.record_check_failure(
            session,
            user_id=user.id,
            failure_kind="unretrieved",
            metric_name=claim.metric,
            stated_value=claim.stated_text,
            expected_value=None,
            response_excerpt=claim.sentence,
        )
    await session.commit()
    return {"ok": False, "text": apply_result(text, result)}
