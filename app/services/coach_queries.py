"""Bounded, deterministic read models used by the coaching chat tools.

The LLM asks for a business question; this module returns a compact, dated answer.
It deliberately exposes no generic database query and never makes training decisions.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo


def clamp_days(value: object, *, default: int, maximum: int) -> int:
    try:
        return max(1, min(int(value or default), maximum))
    except (TypeError, ValueError):
        return default


def hot_training_summary(items: list[object], *, today: date) -> list[str]:
    """Compact facts for every chat turn; details remain available through tools."""
    summaries: list[str] = []
    for days in (7, 28):
        start = today - timedelta(days=days - 1)
        window = [
            item for item in items
            if (item.logged_date if hasattr(item, "logged_date") else item.activity_date) >= start
        ]
        tss = sum(
            float(getattr(item, "tss_actual", None) or getattr(item, "tss", None) or 0)
            for item in window
        )
        if days == 7:
            rpe_known = sum(1 for item in window if getattr(item, "rpe", None) is not None)
            summaries.append(
                f"7 jours : {len(window)} séances, {tss:.0f} TSS, RPE {rpe_known}/{len(window)}"
            )
        else:
            summaries.append(f"28 jours : {len(window)} séances, {tss:.0f} TSS")
    return summaries


async def fitness_history(
    session: AsyncSession, user_id: uuid.UUID, *, days: int, granularity: str
) -> dict:
    """Return source-authoritative CTL/ATL/TSB history, bounded by caller limits."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    rows = await repo.wellness_repo.get_range(session, user_id, start, today)
    points = [r for r in rows if r.ctl is not None and r.atl is not None]
    if granularity == "weekly":
        by_week: dict[tuple[int, int], object] = {}
        for row in points:
            iso = row.date.isocalendar()
            by_week[(iso.year, iso.week)] = row
        points = list(by_week.values())

    rendered = [
        {
            "date": str(row.date),
            "ctl": round(row.ctl, 1),
            "atl": round(row.atl, 1),
            "tsb": round(row.ctl - row.atl, 1),
            "ramp_rate": row.ramp_rate,
        }
        for row in points
    ]
    return {
        "source": "intervals.icu",
        "range_start": str(start),
        "range_end": str(today),
        "granularity": granularity,
        "points": rendered,
        "truncated": False,
    }


async def training_trend(
    session: AsyncSession, user_id: uuid.UUID, *, days: int
) -> dict:
    """Return workload totals by week without asking the model to add activity data."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    logs = await repo.session_log_repo.get_in_range(session, user_id, start, today)
    activities = await repo.activity_repo.get_in_range(session, user_id, start, today)
    if plan is not None:
        activities = [a for a in activities if a.activity_date < plan.start_date]

    weeks: dict[date, dict] = defaultdict(lambda: {"sessions": 0, "tss": 0.0, "minutes": 0})
    for item in [*logs, *activities]:
        item_date = item.logged_date if hasattr(item, "logged_date") else item.activity_date
        week_start = item_date - timedelta(days=item_date.weekday())
        bucket = weeks[week_start]
        bucket["sessions"] += 1
        bucket["tss"] += float(
            getattr(item, "tss_actual", None) or getattr(item, "tss", None) or 0
        )
        seconds = getattr(item, "duration_seconds", None)
        minutes = getattr(item, "duration_minutes_actual", None)
        bucket["minutes"] += int(minutes or (seconds or 0) // 60)

    return {
        "range_start": str(start),
        "range_end": str(today),
        "weeks": [
            {
                "week_start": str(week_start),
                "sessions": values["sessions"],
                "tss": round(values["tss"], 1),
                "minutes": values["minutes"],
            }
            for week_start, values in sorted(weeks.items())
        ],
        "truncated": False,
    }


async def wellness_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int,
    metrics: list[str],
    granularity: str,
) -> dict:
    """Return selected raw wellness signals only; absent readings stay absent."""
    allowed = {"hrv", "resting_hr", "sleep_score", "fatigue", "stress", "motivation", "weight_kg"}
    selected = [m for m in metrics if m in allowed][:4] or ["hrv", "resting_hr"]
    today = date.today()
    start = today - timedelta(days=days - 1)
    rows = await repo.wellness_repo.get_range(session, user_id, start, today)
    if granularity == "weekly":
        by_week: dict[tuple[int, int], object] = {}
        for row in rows:
            iso = row.date.isocalendar()
            by_week[(iso.year, iso.week)] = row
        rows = list(by_week.values())
    return {
        "source": "intervals.icu",
        "range_start": str(start),
        "range_end": str(today),
        "metrics": selected,
        "granularity": granularity,
        "points": [
            {"date": str(row.date), **{metric: getattr(row, metric) for metric in selected}}
            for row in rows
        ],
        "truncated": False,
    }


async def session_detail(
    session: AsyncSession, user_id: uuid.UUID, *, session_date: date
) -> dict:
    """Return one day of activity data. A date can contain a planned-log and a bonus ride."""
    logs = await repo.session_log_repo.get_by_date(session, user_id, session_date)
    activities = await repo.activity_repo.get_in_range(session, user_id, session_date, session_date)
    logged_source_ids = {log.source_activity_id for log in logs if log.source_activity_id}
    activities = [
        activity for activity in activities
        if activity.source_activity_id not in logged_source_ids
    ]
    records: list[dict] = []
    for row in logs:
        records.append({
            "source": row.source,
            "duration_minutes": row.duration_minutes_actual,
            "tss": row.tss_actual,
            "rpe": row.rpe,
            "avg_heart_rate": row.avg_heart_rate,
            "avg_power": row.avg_power,
            "normalized_power": row.normalized_power,
            "intensity_factor": row.intensity_factor,
            "dominant_zone": row.dominant_zone,
            "session_type": row.session_type_real,
            "zone_compliance": row.respect_zones_score,
            "interval_consistency": row.intervals_consistency_index,
            "cardiac_drift": row.cardiac_drift_index,
            "environment": row.environment,
        })
    for row in activities:
        records.append({
            "source": row.source,
            "duration_minutes": row.duration_seconds // 60 if row.duration_seconds else None,
            "tss": row.tss,
            "avg_heart_rate": row.avg_heartrate,
            "avg_power": row.avg_watts,
            "normalized_power": row.normalized_watts,
            "environment": row.environment,
            "elevation_gain_m": row.elevation_gain_meters,
        })
    return {"date": str(session_date), "sessions": records, "truncated": False}
