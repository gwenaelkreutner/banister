import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.weekly_adherence import WeeklyAdherence


async def upsert(
    session: AsyncSession,
    user_id: uuid.UUID,
    week_start_date: date,
    sessions_done: int,
    tss_7d: float,
    plan_id: uuid.UUID | None = None,
    week_number: int | None = None,
    sessions_planned: int | None = None,
    compliance_pct: float | None = None,
) -> None:
    """Insert ou met à jour l'adhérence pour (user_id, week_start_date)."""
    stmt = (
        insert(WeeklyAdherence)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            plan_id=plan_id,
            week_number=week_number,
            week_start_date=week_start_date,
            sessions_done=sessions_done,
            sessions_planned=sessions_planned,
            compliance_pct=compliance_pct,
            tss_7d=tss_7d,
        )
        .on_conflict_do_update(
            constraint="uq_weekly_adherence_user_week",
            set_={
                "plan_id": plan_id,
                "week_number": week_number,
                "sessions_done": sessions_done,
                "sessions_planned": sessions_planned,
                "compliance_pct": compliance_pct,
                "tss_7d": tss_7d,
                "computed_at": datetime.now(timezone.utc),
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


async def get_recent(
    session: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 8,
) -> list[WeeklyAdherence]:
    """Retourne les N dernières semaines, triées du plus récent au plus ancien."""
    result = await session.execute(
        select(WeeklyAdherence)
        .where(WeeklyAdherence.user_id == user_id)
        .order_by(WeeklyAdherence.week_start_date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
