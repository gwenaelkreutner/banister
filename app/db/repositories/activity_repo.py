from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.activity import Activity


async def bulk_insert(session: AsyncSession, user_id, rows: list[dict]) -> int:
    """
    Upsert d'activités Strava.
    Ignore les doublons sur (user_id, source, source_activity_id).
    Retourne le nombre d'activités insérées.
    """
    if not rows:
        return 0

    enriched = [{**r, "user_id": str(user_id)} for r in rows]

    stmt = pg_insert(Activity).values(enriched)
    stmt = stmt.on_conflict_do_nothing()
    result = await session.execute(stmt)
    return result.rowcount


async def get_for_user(session: AsyncSession, user_id, days: int = 49) -> list[Activity]:
    """Retourne les activités des N derniers jours, triées par date croissante."""
    cutoff = date.today() - timedelta(days=days)
    result = await session.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .where(Activity.activity_date >= cutoff)
        .order_by(Activity.activity_date.asc())
    )
    return list(result.scalars().all())


async def clear_for_user(session: AsyncSession, user_id) -> None:
    """Supprime toutes les activités d'un utilisateur (utile pour les tests)."""
    await session.execute(delete(Activity).where(Activity.user_id == user_id))
