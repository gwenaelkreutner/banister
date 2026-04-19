import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models.profile import AthleteProfile


async def get_by_user_id(session: AsyncSession, user_id) -> AthleteProfile | None:
    result = await session.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def create(session: AsyncSession, user_id, profile_data: dict) -> AthleteProfile:
    profile = AthleteProfile(
        id=uuid.uuid4(),
        user_id=user_id,
        profile=profile_data,
    )
    session.add(profile)
    await session.flush()
    return profile


async def update(session: AsyncSession, profile: AthleteProfile, profile_data: dict) -> AthleteProfile:
    profile.profile = profile_data
    await session.flush()
    return profile


async def update_injury_status(session: AsyncSession, user_id, injury_data: dict) -> None:
    """Met à jour le statut de blessure dans le profil athlète."""
    profile = await get_by_user_id(session, user_id)
    if profile is None:
        return
    updated = dict(profile.profile or {})
    updated["injury_status"] = injury_data
    profile.profile = updated
    await session.flush()


async def update_coach_memory(session: AsyncSession, profile: AthleteProfile, memory: list) -> None:
    """Remplace la liste de notes coach_memory."""
    profile.coach_memory = memory
    flag_modified(profile, "coach_memory")
    await session.flush()


async def update_athlete_notes(session: AsyncSession, profile: AthleteProfile, notes: dict) -> None:
    """Remplace le dict de notes stables athlete_notes."""
    profile.athlete_notes = notes
    flag_modified(profile, "athlete_notes")
    await session.flush()
