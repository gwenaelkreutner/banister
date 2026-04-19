import uuid
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class Activity(Base, TimestampMixin):
    """Activité historique importée depuis Strava (ou saisie manuelle future).

    Séparée de session_logs qui est lié aux séances du plan (plan_id NOT NULL).
    Sert de base pour CTL/ATL/TSB et l'auto-détection du niveau à l'onboarding.
    """

    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="strava")
    source_activity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    activity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sport_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'outdoor'|'indoor'

    distance_meters: Mapped[float | None] = mapped_column(nullable=True)
    elevation_gain_meters: Mapped[float | None] = mapped_column(nullable=True)

    avg_watts: Mapped[float | None] = mapped_column(nullable=True)
    normalized_watts: Mapped[float | None] = mapped_column(nullable=True)  # NP
    device_watts: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    avg_heartrate: Mapped[float | None] = mapped_column(nullable=True)
    max_heartrate: Mapped[float | None] = mapped_column(nullable=True)

    suffer_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    kilojoules: Mapped[float | None] = mapped_column(nullable=True)

    tss: Mapped[float | None] = mapped_column(nullable=True)
    tss_method: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 'power'|'hr'|'estimation'
    ftp_used: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    user: Mapped["User"] = relationship(back_populates="activities")  # noqa: F821
