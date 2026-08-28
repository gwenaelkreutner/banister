"""Calendar-publication persistence (spec 005 data-model.md).

Unlike spec 004 (pure Pydantic-in-JSON, no migration), this feature needs two real
tables. The reason is FR-016: an interrupted publication must be resumable, which means
what we published — and *which approval authorised it* (FR-005) — has to survive a
restart. Re-deriving it from the calendar alone would lose the approval linkage.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, SmallInteger, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.types import UtcDateTime


class PublicationApproval(Base):
    """The athlete's recorded consent to write a specific set of sessions on specific
    dates. Bound to exactly what was shown (FR-004) — this is the entity that makes
    "was this written with consent?" answerable after the fact (spec 001 FR-019a).

    States: pending -> approved | declined. Terminal either way. A declined approval is
    kept, not deleted — FR-003 requires not re-asking unprompted, and a deleted record
    cannot express "they already said no."
    """

    __tablename__ = "publication_approvals"
    __table_args__ = (
        Index("idx_publication_approvals_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 over exactly what the athlete was shown — see services/publication.py.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The bounded period (FR-010), recorded so it can be stated back.
    horizon_start: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end: Mapped[date] = mapped_column(Date, nullable=False)
    # How many sessions were in the request, for the summary report (FR-006).
    session_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )  # "pending" | "approved" | "declined"
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class PublishedEntry(Base):
    """One session written to the athlete's calendar, identifiable as ours (via
    external_id) and traceable back to its planned SessionSpec (FR-013).

    Withdrawn rows are kept as history: FR-024 says an entry the athlete deleted must
    not be silently recreated, and telling "we withdrew this" from "the athlete deleted
    this" from "we never published this" needs all three states representable.
    """

    __tablename__ = "published_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_published_entries_user_external_id"),
        Index("idx_published_entries_plan", "plan_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("training_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("publication_approvals.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Our ownership marker — see data-model.md §External id. Unique per user.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # The remote event id, required for PUT / DELETE (research R2). String, not numeric:
    # the source's ids are not guaranteed numeric elsewhere in this codebase.
    intervals_event_id: Mapped[str] = mapped_column(String(32), nullable=False)

    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    week_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Lun, 6=Dim

    # Hash of what we actually wrote — the basis for divergence detection (FR-020).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
