import uuid
from datetime import UTC, date, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.activity import Activity
from app.db.models.chat_message import ChatMessage
from app.db.models.guardrail import GuardrailAcknowledgement, ResponseCheckFailure
from app.db.models.profile import AthleteProfile
from app.db.models.publication import PublicationApproval, PublishedEntry
from app.db.models.session_log import SessionLog
from app.db.models.training_plan import TrainingPlan
from app.db.models.user import User
from app.db.models.weekly_adherence import WeeklyAdherence


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


# ── spec 007 ────────────────────────────────────────────────────────────────


async def set_coach_voice(session: AsyncSession, user: User, voice_id: str | None) -> None:
    """`None` clears the override → the athlete falls back to `settings.persona` (FR-024).
    A single UPDATE — no existing record is touched (FR-025)."""
    user.coach_voice = voice_id
    await session.flush()


async def ack_disclaimer(session: AsyncSession, user: User) -> None:
    """Called right after the disclaimer is shown, so it is shown exactly once (FR-028).
    Idempotent — a second call leaves the first timestamp in place."""
    if user.disclaimer_acknowledged_at is None:
        user.disclaimer_acknowledged_at = datetime.now(UTC)
        await session.flush()


# Every per-athlete row `/reset` deletes. `coach_voice` and `disclaimer_acknowledged_at`
# live on `users` and are deliberately NOT here — they are identity, not training data
# (FR-020: a reset athlete keeps their chosen voice and is not re-shown the disclaimer).
_PURGE_MODELS = (
    ResponseCheckFailure,
    GuardrailAcknowledgement,
    PublishedEntry,
    PublicationApproval,
    WeeklyAdherence,
    ChatMessage,
    SessionLog,
    Activity,
    TrainingPlan,
    AthleteProfile,
)


async def purge_athlete_data(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Delete every per-athlete row for `/reset` (spec 007 US4, FR-020, SC-006/SC-007).

    Local only — issues no outbound call, and never touches intervals.icu. The `User`
    row survives (telegram_id, first_name, coach_voice, disclaimer ack, reminder prefs);
    `onboarding_completed_at` is cleared by the caller so the next /setup is a real first
    run. Returns a per-table deleted count, for the confirmation message.
    """
    counts: dict[str, int] = {}
    for model in _PURGE_MODELS:
        result = await session.execute(
            delete(model).where(model.user_id == user_id)
        )
        counts[model.__tablename__] = result.rowcount or 0
    await session.flush()
    return counts
