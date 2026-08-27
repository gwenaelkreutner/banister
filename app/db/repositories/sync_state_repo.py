import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.sync_state import ReportedActivity, SyncState
from app.db.upsert import dialect_insert


async def is_reported(session: AsyncSession, user_id: uuid.UUID, source_activity_id: str) -> bool:
    result = await session.execute(
        select(ReportedActivity.id).where(
            ReportedActivity.user_id == user_id,
            ReportedActivity.source_activity_id == source_activity_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_reported(
    session: AsyncSession,
    user_id: uuid.UUID,
    source_activity_id: str,
    *,
    reported_at: datetime,
) -> None:
    """Écrit le marqueur — à appeler seulement après l'envoi effectif de la notification
    (FR-012). Idempotent : un second appel pour la même activité ne duplique rien."""
    stmt = (
        dialect_insert(session)(ReportedActivity)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            source_activity_id=source_activity_id,
            reported_at=reported_at,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source_activity_id"])
    )
    await session.execute(stmt)
    await session.flush()


async def get_or_create_sync_state(session: AsyncSession, user_id: uuid.UUID) -> SyncState:
    result = await session.execute(select(SyncState).where(SyncState.user_id == user_id))
    state = result.scalar_one_or_none()
    if state is not None:
        return state

    state = SyncState(id=uuid.uuid4(), user_id=user_id)
    session.add(state)
    await session.flush()
    return state


async def update_last_successful_refresh(
    session: AsyncSession, user_id: uuid.UUID, when: datetime
) -> None:
    state = await get_or_create_sync_state(session, user_id)
    state.last_successful_refresh_at = when
    await session.flush()


async def advance_history_import_cursor(
    session: AsyncSession, user_id: uuid.UUID, oldest_date_reached: date
) -> None:
    """Fait avancer le curseur d'import vers le passé — reprendre après une interruption
    part de cette valeur plutôt que de tout réimporter (FR-027)."""
    state = await get_or_create_sync_state(session, user_id)
    state.history_import_cursor_date = oldest_date_reached
    await session.flush()


async def mark_history_import_complete(session: AsyncSession, user_id: uuid.UUID) -> None:
    state = await get_or_create_sync_state(session, user_id)
    state.history_import_complete = True
    await session.flush()
