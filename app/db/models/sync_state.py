import uuid
from datetime import date as date_
from datetime import datetime

from sqlalchemy import Boolean, Date, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin
from app.db.types import UtcDateTime


class ReportedActivity(Base):
    """Durable marker that an activity has already been announced to the athlete (spec
    002 FR-009, FR-010, FR-012).

    Keyed by the source's own activity id — stable across edits/renames, which is what
    makes FR-010 ("an edited activity is not a new activity") free rather than something
    the poller has to detect itself. A row here must only ever be written *after* the
    notification has actually been delivered (FR-012): writing it first would let a
    delivery failure between the write and the send permanently silence that activity.
    """

    __tablename__ = "reported_activities"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_activity_id", name="uq_reported_activity_user_source_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_activity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class SyncState(Base, TimestampMixin):
    """One row per athlete: when the source was last successfully polled (FR-021 — lets
    a stale-data warning state its own age), and how far the first-connection history
    import has progressed (FR-026..029).

    `history_import_cursor_date` is the oldest date the import has successfully reached
    so far, walking backwards from "today" — an interruption resumes from this cursor
    rather than restarting (FR-027), and `history_import_complete` is what lets the
    athlete be told history is still incomplete rather than presented as final (FR-028).
    """

    __tablename__ = "sync_state"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    last_successful_refresh_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    history_import_cursor_date: Mapped[date_ | None] = mapped_column(Date, nullable=True)
    history_import_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
