import uuid
from datetime import date as date_

from sqlalchemy import Date, Float, ForeignKey, Integer, SmallInteger, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin


class Wellness(Base, TimestampMixin):
    """Dated recovery signals from intervals.icu (spec 002 FR-023, FR-024).

    Every signal is nullable, deliberately: a missing HRV reading stored as `0` would
    later read as a catastrophic drop once spec 006 evaluates readiness thresholds
    against it — the exact failure FR-024 exists to prevent. Capture only; no
    interpretation happens here.

    `ramp_rate` (spec 006 T003, research R4): the source's own CTL gain per week,
    consumed as-is, never recomputed (Constitution Principle IV). Nullable for the same
    reason as every other column here — absent must stay distinguishable from zero.
    Spec 006's guardrail evaluators interpret these numbers; this model still does not.
    """

    __tablename__ = "wellness"
    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_wellness_user_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date_] = mapped_column(Date, nullable=False)

    hrv: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sleep_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctl: Mapped[float | None] = mapped_column(Float, nullable=True)
    atl: Mapped[float | None] = mapped_column(Float, nullable=True)
    # source's own CTL gain per week, consumed as-is (spec 006 R4)
    ramp_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
