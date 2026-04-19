import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


async def get_single_user(session: AsyncSession) -> User | None:
    result = await session.execute(select(User).limit(1))
    return result.scalar_one_or_none()


async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def create_single_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
    )
    session.add(user)
    await session.flush()
    return user


async def get_users_to_remind(session: AsyncSession, hour: int, minute: int) -> list[User]:
    """Retourne les users actifs dont le rappel est dû à cette heure (UTC+1) et pas encore envoyé aujourd'hui."""
    today = date.today()
    result = await session.execute(
        select(User).where(
            User.is_active == True,
            User.reminders_enabled == True,
            User.reminder_hour == hour,
            User.reminder_minute == minute,
            or_(User.reminder_last_sent_at.is_(None), User.reminder_last_sent_at < today),
        )
    )
    return list(result.scalars().all())


async def update_reminder_settings(
    session: AsyncSession,
    user: User,
    *,
    enabled: bool | None = None,
    hour: int | None = None,
    minute: int | None = None,
    last_sent_at: date | None = None,
) -> None:
    if enabled is not None:
        user.reminders_enabled = enabled
    if hour is not None:
        user.reminder_hour = hour
    if minute is not None:
        user.reminder_minute = minute
    if last_sent_at is not None:
        user.reminder_last_sent_at = last_sent_at
    await session.flush()
