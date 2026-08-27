import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.wellness import Wellness
from app.db.upsert import dialect_insert


async def upsert(
    session: AsyncSession,
    user_id: uuid.UUID,
    date_: date,
    *,
    hrv: float | None = None,
    resting_hr: int | None = None,
    sleep_seconds: int | None = None,
    weight_kg: float | None = None,
    ctl: float | None = None,
    atl: float | None = None,
) -> None:
    """Insert ou met à jour le bien-être pour (user_id, date_). Chaque signal absent de la
    source reste `None` ici — jamais réécrit en `0` (FR-024)."""
    stmt = (
        dialect_insert(session)(Wellness)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            date=date_,
            hrv=hrv,
            resting_hr=resting_hr,
            sleep_seconds=sleep_seconds,
            weight_kg=weight_kg,
            ctl=ctl,
            atl=atl,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "date"],
            set_={
                "hrv": hrv,
                "resting_hr": resting_hr,
                "sleep_seconds": sleep_seconds,
                "weight_kg": weight_kg,
                "ctl": ctl,
                "atl": atl,
            },
        )
    )
    await session.execute(stmt)
    await session.flush()


async def get_by_date(session: AsyncSession, user_id: uuid.UUID, date_: date) -> Wellness | None:
    result = await session.execute(
        select(Wellness).where(Wellness.user_id == user_id, Wellness.date == date_)
    )
    return result.scalar_one_or_none()


async def get_range(
    session: AsyncSession,
    user_id: uuid.UUID,
    start: date,
    end: date,
) -> list[Wellness]:
    """Retourne les enregistrements dans [start, end], triés du plus ancien au plus récent."""
    result = await session.execute(
        select(Wellness)
        .where(Wellness.user_id == user_id, Wellness.date >= start, Wellness.date <= end)
        .order_by(Wellness.date.asc())
    )
    return list(result.scalars().all())
