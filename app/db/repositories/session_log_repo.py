import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session_log import SessionLog


async def create(
    session: AsyncSession,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    week_number: int,
    day_of_week: int,
    logged_date: date,
    status: str,
    rpe_emoji: str | None = None,
    duration_minutes_actual: int | None = None,
    tss_actual: float | None = None,
    strava_activity_id: int | None = None,
    source: str = "manual",
    avg_heart_rate: int | None = None,
    avg_power: int | None = None,
    normalized_power: int | None = None,
    kilojoules: float | None = None,
    environment: str | None = None,
    time_in_zones_s: dict | None = None,
    cardiac_drift_index: float | None = None,
    intervals_consistency_index: float | None = None,
    respect_zones_score: float | None = None,
    session_type_real: str | None = None,
    variability_index: float | None = None,
    intensity_factor: float | None = None,
    dominant_zone: str | None = None,
    elevation_gain_m: float | None = None,
    average_temp_c: float | None = None,
    athlete_count: int | None = None,
    ctl_at_session: float | None = None,
    atl_at_session: float | None = None,
    tsb_at_session: float | None = None,
    kpi_contribution: float | None = None,
) -> SessionLog:
    log = SessionLog(
        id=uuid.uuid4(),
        user_id=user_id,
        plan_id=plan_id,
        week_number=week_number,
        day_of_week=day_of_week,
        logged_date=logged_date,
        status=status,
        rpe_emoji=rpe_emoji,
        duration_minutes_actual=duration_minutes_actual,
        tss_actual=tss_actual,
        strava_activity_id=strava_activity_id,
        source=source,
        avg_heart_rate=avg_heart_rate,
        avg_power=avg_power,
        normalized_power=normalized_power,
        kilojoules=kilojoules,
        environment=environment,
        time_in_zones_s=time_in_zones_s,
        cardiac_drift_index=cardiac_drift_index,
        intervals_consistency_index=intervals_consistency_index,
        respect_zones_score=respect_zones_score,
        session_type_real=session_type_real,
        variability_index=variability_index,
        intensity_factor=intensity_factor,
        dominant_zone=dominant_zone,
        elevation_gain_m=elevation_gain_m,
        average_temp_c=average_temp_c,
        athlete_count=athlete_count,
        ctl_at_session=ctl_at_session,
        atl_at_session=atl_at_session,
        tsb_at_session=tsb_at_session,
        kpi_contribution=kpi_contribution,
    )
    session.add(log)
    await session.flush()
    return log


async def get_by_date(
    session: AsyncSession,
    user_id: uuid.UUID,
    logged_date: date,
) -> list[SessionLog]:
    result = await session.execute(
        select(SessionLog)
        .where(SessionLog.user_id == user_id, SessionLog.logged_date == logged_date)
        .order_by(SessionLog.created_at)
    )
    return list(result.scalars().all())


async def get_all_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> list[SessionLog]:
    """Retourne tous les logs de l'utilisateur, triés par date (pour ATL/CTL).

    Inclut "done" (séances du plan) et "unplanned" (activités Strava hors-plan
    avec TSS calculé) — les deux contribuent à la charge réelle.
    """
    result = await session.execute(
        select(SessionLog)
        .where(
            SessionLog.user_id == user_id,
            SessionLog.status.in_(["done", "unplanned"]),
        )
        .order_by(SessionLog.logged_date)
    )
    return list(result.scalars().all())


async def already_logged(
    session: AsyncSession,
    user_id: uuid.UUID,
    week_number: int,
    day_of_week: int,
) -> bool:
    result = await session.execute(
        select(SessionLog).where(
            SessionLog.user_id == user_id,
            SessionLog.week_number == week_number,
            SessionLog.day_of_week == day_of_week,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_by_strava_activity(
    session: AsyncSession,
    strava_activity_id: int,
) -> SessionLog | None:
    result = await session.execute(
        select(SessionLog).where(SessionLog.strava_activity_id == strava_activity_id)
    )
    return result.scalar_one_or_none()
