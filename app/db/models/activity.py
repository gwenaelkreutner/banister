import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class Activity(Base, TimestampMixin):
    """Activité historique importée depuis la source de données d'entraînement.

    Séparée de session_logs qui est lié aux séances du plan (plan_id NOT NULL).
    Sert de base pour CTL/ATL/TSB et l'auto-détection du niveau à l'onboarding.
    """

    __tablename__ = "activities"
    __table_args__ = (
        # This partial unique index existed only in migrations/init.sql, not in the ORM
        # model — found while porting bulk_insert's upsert (spec 003 T018). Without it here,
        # Alembic's baseline (Phase 4) would silently omit it, and on_conflict_do_nothing
        # would have no constraint to target. `text(...)` renders identically on SQLite and
        # PostgreSQL, verified empirically.
        Index(
            "idx_activities_source_id",
            "user_id",
            "source",
            "source_activity_id",
            unique=True,
            postgresql_where=text("source_activity_id IS NOT NULL"),
            sqlite_where=text("source_activity_id IS NOT NULL"),
        ),
        # Present in migrations/init.sql, absent from this model until spec 003's
        # structural fidelity pass (T024).
        Index("idx_activities_user_date", "user_id", "activity_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="intervals_icu")
    # String, not numeric: an opaque external activity id should never have been typed
    # as BigInteger to begin with — intervals.icu's ids are not numeric (e.g.
    # "i180170537", confirmed against the live account).
    source_activity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

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
