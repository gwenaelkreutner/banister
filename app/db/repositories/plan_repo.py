import uuid
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.training_plan import TrainingPlan


async def get_active_plan(session: AsyncSession, user_id) -> TrainingPlan | None:
    result = await session.execute(
        select(TrainingPlan)
        .where(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
        .order_by(TrainingPlan.created_at.desc())
    )
    return result.scalar_one_or_none()


async def deactivate_all_for_user(session: AsyncSession, user_id) -> None:
    """Marque tous les plans actifs de l'utilisateur comme inactifs.

    Bug réel trouvé en conditions réelles (2026-08-28) : `_finalize_setup()` avait
    déjà le commentaire "Deactivate old plans, then create new one" mais aucun code
    ne le faisait — chaque `/setup` créait un nouveau plan `status="active"` sans
    jamais désactiver le précédent. `get_active_plan()` suppose un seul résultat
    (`scalar_one_or_none`), donc un deuxième `/setup` faisait planter silencieusement
    tout appelant de `get_active_plan()` (`/plan`, `/forme`, `/recap`, le chat...)
    avec `MultipleResultsFound`, sans message d'erreur visible pour l'athlète."""
    await session.execute(
        update(TrainingPlan)
        .where(TrainingPlan.user_id == user_id, TrainingPlan.status == "active")
        .values(status="inactive")
    )
    await session.flush()


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


async def set_start_date(session: AsyncSession, plan: TrainingPlan, start_date: date) -> None:
    """Met à jour la date de début du plan."""
    plan.start_date = start_date
    await session.flush()
