import uuid
from datetime import date

from sqlalchemy import JSON, BigInteger, Date, ForeignKey, SmallInteger, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class SessionLog(Base, TimestampMixin):
    """Log d'une séance réalisée ou sautée."""

    __tablename__ = "session_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("training_plans.id", ondelete="CASCADE"), nullable=False
    )

    week_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Lun, 6=Dim
    logged_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)  # "done" | "skipped" | "unplanned"
    rpe_emoji: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "hard"|"normal"|"easy"
    duration_minutes_actual: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    tss_actual: Mapped[float | None] = mapped_column(nullable=True)
    strava_activity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # "manual"|"strava"

    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "outdoor" | "indoor"

    # Données physiologiques (Strava)
    avg_heart_rate: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    avg_power: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)        # average_watts
    normalized_power: Mapped[int | None] = mapped_column(SmallInteger, nullable=True) # weighted_average_watts
    kilojoules: Mapped[float | None] = mapped_column(nullable=True)
    time_in_zones_s: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Métriques qualité calculées au moment du webhook (absent pour les logs manuels)
    cardiac_drift_index: Mapped[float | None] = mapped_column(nullable=True)
    intervals_consistency_index: Mapped[float | None] = mapped_column(nullable=True)
    respect_zones_score: Mapped[float | None] = mapped_column(nullable=True)
    session_type_real: Mapped[str | None] = mapped_column(String(16), nullable=True)
    variability_index: Mapped[float | None] = mapped_column(nullable=True)
    intensity_factor: Mapped[float | None] = mapped_column(nullable=True)  # FTP de l'époque, immuable
    dominant_zone: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # Contexte Strava (absent pour les logs manuels)
    elevation_gain_m: Mapped[float | None] = mapped_column(nullable=True)
    average_temp_c: Mapped[float | None] = mapped_column(nullable=True)
    athlete_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Snapshots ATL/CTL/TSB au moment de l'enregistrement (non-autoritaires)
    ctl_at_session: Mapped[float | None] = mapped_column(nullable=True)
    atl_at_session: Mapped[float | None] = mapped_column(nullable=True)
    tsb_at_session: Mapped[float | None] = mapped_column(nullable=True)

    # KPI d'adhérence : points gagnés pour cette séance (0..~2.0)
    # Cumulatif plan = SUM(kpi_contribution) borné à 100
    kpi_contribution: Mapped[float | None] = mapped_column(nullable=True)

    user: Mapped["User"] = relationship(back_populates="session_logs")  # noqa: F821
