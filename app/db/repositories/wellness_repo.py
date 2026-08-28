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
    ramp_rate: float | None = None,
) -> None:
    """Insert ou met à jour le bien-être pour (user_id, date_). Chaque signal absent de la
    source reste `None` ici — jamais réécrit en `0` (FR-024). `ramp_rate` : gain de CTL
    par semaine calculé par la source, consommé tel quel (spec 006 R4)."""
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
            ramp_rate=ramp_rate,
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
                "ramp_rate": ramp_rate,
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


async def get_latest(
    session: AsyncSession, user_id: uuid.UUID, *, on_or_before: date
) -> Wellness | None:
    """Le wellness le plus récent avec un CTL renseigné, à date `on_or_before` ou avant.

    intervals.icu calcule CTL/ATL chaque jour, y compris les jours de repos — contrairement
    à un CTL dérivé des seules activités, qui resterait figé tant qu'aucune sortie n'a lieu.
    `ctl IS NOT NULL` exclut les jours capturés avant que la source ait fini son calcul du
    jour (constaté en conditions réelles : le poller peut tourner avant que intervals.icu
    n'ait recalculé la nuit précédente)."""
    result = await session.execute(
        select(Wellness)
        .where(
            Wellness.user_id == user_id,
            Wellness.date <= on_or_before,
            Wellness.ctl.is_not(None),
            Wellness.atl.is_not(None),
        )
        .order_by(Wellness.date.desc())
        .limit(1)
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
