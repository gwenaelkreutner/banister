import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.training_plan import TrainingPlan


async def get_active_plan(session: AsyncSession, user_id) -> TrainingPlan | None:
    result = await session.execute(
        select(TrainingPlan)
        .where(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
        .order_by(TrainingPlan.created_at.desc())
    )
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    user_id,
    plan_technical: dict,
    start_date: date,
    end_date: date,
    block_number: int = 1,
) -> TrainingPlan:
    plan = TrainingPlan(
        id=uuid.uuid4(),
        user_id=user_id,
        plan_technical=plan_technical,
        plan_narrative={},
        start_date=start_date,
        end_date=end_date,
        block_number=block_number,
        status="active",
    )
    session.add(plan)
    await session.flush()
    return plan


async def update_narrative(
    session: AsyncSession, plan: TrainingPlan, narrative: dict
) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    plan.plan_narrative = narrative
    flag_modified(plan, "plan_narrative")
    await session.flush()


async def get_all_active(session: AsyncSession) -> list[TrainingPlan]:
    """Retourne tous les plans actifs (utilisé par le webhook Strava)."""
    result = await session.execute(
        select(TrainingPlan).where(TrainingPlan.status == "active")
    )
    return list(result.scalars().all())


async def set_start_date(session: AsyncSession, plan: TrainingPlan, start_date: date) -> None:
    """Met à jour la date de début du plan."""
    plan.start_date = start_date
    await session.flush()
