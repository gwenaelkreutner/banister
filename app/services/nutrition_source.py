"""Read-only access to the athlete's food intake. Banister never logs or estimates meals.

Two sources, no third party required:
- `NUTRITION_API_URL` set: any service implementing docs/NUTRITION_INTEGRATION.md;
- otherwise: the nutrition fields intervals.icu holds, already ingested into `wellness`.

A source that fails answers `available: false`, so coaching simply carries on without it. A day
with nothing logged keeps `kcal: None`: it is never reported as zero.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.time import paris_today
from app.db import repositories as repo

_TIMEOUT_S = 4.0
_FIELDS = (
    "date", "status", "kcal", "kcal_min", "kcal_max", "protein_g", "carbs_g", "fat_g",
    "hydration_ml",
)
_NATURE = "estimates, not measurements; null kcal means nothing logged, not zero"


async def _from_service(start: date, end: date) -> dict:
    headers = {}
    token = settings.nutrition_api_token.get_secret_value()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{settings.nutrition_api_url.rstrip('/')}/v1/days"
    params = {"from": start.isoformat(), "to": end.isoformat()}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            rows = response.json()
        days = [{k: row.get(k) for k in _FIELDS} for row in rows]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        return {"available": False, "reason": f"nutrition source failed ({exc.__class__.__name__})"}
    return {"available": True, "source": "nutrition_service", "nature": _NATURE, "days": days}


async def _from_wellness(session: AsyncSession, user_id: uuid.UUID, start: date, end: date) -> dict:
    rows = await repo.wellness_repo.get_range(session, user_id, start, end)
    by_date = {row.date: row for row in rows}
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        row = by_date.get(day)
        kcal = row.kcal_consumed if row else None
        volume = row.hydration_volume_l if row else None
        days.append({
            "date": day.isoformat(),
            "status": "complete" if kcal is not None else "empty",
            "kcal": kcal,
            "kcal_min": None,
            "kcal_max": None,
            "protein_g": row.protein_g if row else None,
            "carbs_g": row.carbohydrates_g if row else None,
            "fat_g": row.fat_g if row else None,
            "hydration_ml": round(volume * 1000) if volume is not None else None,
        })
    return {"available": True, "source": "intervals_wellness", "nature": _NATURE, "days": days}


async def daily_summary(session: AsyncSession, user_id: uuid.UUID, days: int) -> dict:
    end = paris_today()
    start = end - timedelta(days=days - 1)
    if settings.nutrition_api_url:
        return await _from_service(start, end)
    return await _from_wellness(session, user_id, start, end)
