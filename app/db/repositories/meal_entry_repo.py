"""Persistence for daily calorie tracking (spec 008 data-model.md).

Five narrow functions, no generic CRUD abstraction — mirrors `guardrail_repo.py`'s size.
`daily_totals` is the one aggregation query every consumer (the `log_meal` tool,
`get_calorie_history`, the evening reminder) actually needs: a day absent from its result
has zero entries, never a `0`-total row (FR-008) — callers must not confuse the two.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.meal_entry import MealEntry


@dataclass(frozen=True)
class DailyCalorieTotal:
    entry_date: date
    total_calories: int
    entry_count: int


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    entry_date: date,
    entry_type: str,
    meal_slot: str | None,
    raw_description: str,
    estimated_calories: int,
) -> MealEntry:
    entry = MealEntry(
        id=uuid.uuid4(),
        user_id=user_id,
        entry_date=entry_date,
        entry_type=entry_type,
        meal_slot=meal_slot,
        raw_description=raw_description,
        estimated_calories=estimated_calories,
        # Set explicitly (not left to TimestampMixin's server_default=func.now()):
        # SQLite's CURRENT_TIMESTAMP is second-resolution, and get_latest_for_date orders
        # by this column — two entries logged seconds apart (a realistic same-session
        # correction) would tie and return an arbitrary row. Python's datetime.now(UTC)
        # has microsecond resolution, same fix guardrail_repo.py already applies for the
        # same reason (occurred_at/decided_at).
        created_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def delete_for_date(session: AsyncSession, user_id: uuid.UUID, entry_date: date) -> int:
    """Supprime toutes les entrées de ce jour (remplacement par un récap — FR-009).
    Retourne le nombre de lignes supprimées, pour que l'appelant sache s'il y avait
    quelque chose à remplacer."""
    result = await session.execute(
        select(MealEntry).where(
            MealEntry.user_id == user_id, MealEntry.entry_date == entry_date
        )
    )
    entries = list(result.scalars().all())
    for entry in entries:
        await session.delete(entry)
    await session.flush()
    return len(entries)


async def get_latest_for_date(
    session: AsyncSession, user_id: uuid.UUID, entry_date: date
) -> MealEntry | None:
    result = await session.execute(
        select(MealEntry)
        .where(MealEntry.user_id == user_id, MealEntry.entry_date == entry_date)
        .order_by(MealEntry.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def delete(session: AsyncSession, entry: MealEntry) -> None:
    await session.delete(entry)
    await session.flush()


async def daily_totals(
    session: AsyncSession, user_id: uuid.UUID, start_date: date, end_date: date
) -> list[DailyCalorieTotal]:
    """Un `DailyCalorieTotal` par jour ayant au moins une entrée dans [start_date,
    end_date] — un jour sans ligne est absent du résultat, jamais présent à 0 (FR-008)."""
    result = await session.execute(
        select(
            MealEntry.entry_date,
            func.sum(MealEntry.estimated_calories),
            func.count(MealEntry.id),
        )
        .where(
            MealEntry.user_id == user_id,
            MealEntry.entry_date >= start_date,
            MealEntry.entry_date <= end_date,
        )
        .group_by(MealEntry.entry_date)
        .order_by(MealEntry.entry_date)
    )
    return [
        DailyCalorieTotal(entry_date=d, total_calories=int(total), entry_count=int(count))
        for d, total, count in result.all()
    ]
