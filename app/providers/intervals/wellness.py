"""intervals.icu wellness ingestion (spec 002 T029, FR-023, FR-024, FR-025).

Capture only: this stores whatever the source has for each day, nullable field by
nullable field, and interprets none of it. Turning these numbers into readiness
guardrails is explicitly out of scope here (FR-025) — that is spec 006's job, and only
once there is athlete data to evaluate (research R9d found this account's wellness
entirely empty across every sampled day).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import wellness_repo
from app.providers.intervals.client import IntervalsClient


def _parse_date(record_id: str) -> date:
    return datetime.strptime(record_id, "%Y-%m-%d").date()


async def ingest_wellness(
    session: AsyncSession,
    user_id: uuid.UUID,
    client: IntervalsClient,
    *,
    oldest: str,
    newest: str,
) -> int:
    """Fetch wellness records in [oldest, newest] and upsert each one. Returns the
    number of days ingested."""
    records = await client.list_wellness(oldest=oldest, newest=newest)

    for record in records:
        await wellness_repo.upsert(
            session,
            user_id,
            _parse_date(record["id"]),
            hrv=record.get("hrv"),
            resting_hr=record.get("restingHR"),
            sleep_seconds=record.get("sleepSecs"),
            weight_kg=record.get("weight"),
            ctl=record.get("ctl"),
            atl=record.get("atl"),
            ramp_rate=record.get("rampRate"),
        )

    return len(records)
