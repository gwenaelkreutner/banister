import uuid
from datetime import date, datetime

from sqlalchemy import Date, Float, ForeignKey, SmallInteger, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.types import UtcDateTime


class WeeklyAdherence(Base):
    """Snapshot d'adhérence hebdomadaire — persisté à chaque /recap."""

    __tablename__ = "weekly_adherence"
    __table_args__ = (UniqueConstraint("user_id", "week_start_date", name="uq_weekly_adherence_user_week"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("training_plans.id", ondelete="SET NULL"), nullable=True
    )
    week_number: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    sessions_done: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sessions_planned: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    compliance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    tss_7d: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )
