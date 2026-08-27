from datetime import date, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.activity import Activity
from app.db.upsert import dialect_insert


async def bulk_insert(session: AsyncSession, user_id, rows: list[dict]) -> int:
    """
    Upsert d'activités Strava.
    Ignore les doublons sur (user_id, source, source_activity_id) — cible le même index
    unique partiel que le modèle déclare (spec 003: ce n'était auparavant qu'implicite,
    l'ancien on_conflict_do_nothing() sans cible ne visait aucune contrainte réelle).
    Retourne le nombre d'activités insérées.
    """
    if not rows:
        return 0

    # Bind the real UUID object, not its string form: the generic Uuid(as_uuid=True) type
    # (spec 003) expects a uuid.UUID to bind, where the previous PostgreSQL driver's
    # leniency toward text representations had masked this. Found by test_upsert.py
    # failing on SQLite while passing on PostgreSQL, during the port — the exact class of
    # dialect-specific tolerance the portability work existed to surface.
    enriched = [{**r, "user_id": user_id} for r in rows]

    stmt = dialect_insert(session)(Activity).values(enriched)
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "source", "source_activity_id"],
        index_where=text("source_activity_id IS NOT NULL"),
    )
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
