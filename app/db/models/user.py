import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, SmallInteger, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin
from app.db.types import UtcDateTime


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Set once, at the end of /setup — found missing entirely (not a column, not a
    # property) while live-testing this app for the first time: every handler below
    # /start that gated on `user.onboarding_completed` would have raised AttributeError.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Rappels de séance
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    reminder_hour: Mapped[int] = mapped_column(SmallInteger, default=7)
    reminder_minute: Mapped[int] = mapped_column(SmallInteger, default=30)
    reminder_last_sent_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    @property
    def onboarding_completed(self) -> bool:
        return self.onboarding_completed_at is not None

    profile: Mapped["AthleteProfile | None"] = relationship(back_populates="user", uselist=False)  # noqa: F821
    training_plans: Mapped[list["TrainingPlan"]] = relationship(back_populates="user")  # noqa: F821
    session_logs: Mapped[list["SessionLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
    activities: Mapped[list["Activity"]] = relationship(back_populates="user", cascade="all, delete-orphan")  # noqa: F821
