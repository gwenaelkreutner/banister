import uuid
from datetime import date as date_

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
)
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

    # Reste des champs bruts intervals.icu /wellness (Section11-inspired, 2026-09-21) —
    # même payload que hrv/resting_hr/... ci-dessus, simplement jamais persisté avant.
    # Échelle 1-4 pour les champs catégoriels (1 = meilleur état) : sleep_quality, fatigue,
    # soreness, stress, mood, motivation, injury, hydration — sens toujours cohérent, label
    # différent par champ (voir intervals.icu). Tout nullable, même raison que ci-dessus.
    hrv_sdnn: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_quality: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sleep_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    mental_energy: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    avg_sleeping_hr: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    fatigue: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    soreness: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    stress: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    mood: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    motivation: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    injury: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    hydration: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    blood_glucose: Mapped[float | None] = mapped_column(Float, nullable=True)
    systolic: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    diastolic: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    baevsky_si: Mapped[float | None] = mapped_column(Float, nullable=True)
    lactate: Mapped[float | None] = mapped_column(Float, nullable=True)
    respiration: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    abdomen_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hydration_volume_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    kcal_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carbohydrates_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "PERIOD" | "FOLLICULAR" | "OVULATION" | "LUTEAL" ... — pas sur l'échelle 1-4.
    menstrual_phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    menstrual_phase_predicted: Mapped[str | None] = mapped_column(String(16), nullable=True)
    readiness: Mapped[float | None] = mapped_column(Float, nullable=True)
