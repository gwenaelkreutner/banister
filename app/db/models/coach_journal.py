"""Dated event journal (Enduragent parity review, 2026-09-20) — persistence for
`CoachJournalEntry`.

Separate from `AthleteProfile.coach_memory` (JSON, capped at 15, always injected into
every chat turn's system prompt): this table is the medium-term store the coach's
`memory_query` tool searches on demand. An `add_note` call that evicts the oldest
`coach_memory` entry past the cap no longer loses that fact — it stays queryable here
indefinitely (app/llm/chat.py::_tool_update_coach_memory writes both in one call).

Modeled on `ResponseCheckFailure` (app/db/models/guardrail.py), not `MealEntry`: a record
of something that *happened*, never updated after insert, so no `TimestampMixin` (its
`updated_at` would be a lie on an append-only row) — a single `occurred_at` instead.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base
from app.db.types import UtcDateTime


class CoachJournalEntry(Base):
    """One dated event: an LLM-authored durable note (`source="llm"`, same categories as
    `update_coach_memory`'s `add_note`: fatigue/motivation/physique/event/preference) or a
    deterministic hook (`source="deterministic"`, category goal_change/injury/
    freestyle_toggle — text is Python-templated from already-validated data, never free
    LLM prose). Routine training data (sessions, RPE, guardrail decisions) is deliberately
    never journaled here — already structured in session_logs/guardrail_acknowledgements,
    and Enduragent's own ledger_append tool description states the same exclusion
    ("skip routine training data and anything already in Athlete Context")."""

    __tablename__ = "coach_journal_entries"
    __table_args__ = (
        Index("idx_coach_journal_user_date", "user_id", "entry_date"),
        # Dedup target for on_conflict_do_nothing (journal_repo.create), same pattern as
        # activity_repo.bulk_insert() — protects against a retried tool call or a hook
        # firing twice writing the same fact twice.
        Index(
            "uq_coach_journal_dedup", "user_id", "entry_date", "category", "dedup_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entry_date: Mapped[date] = mapped_column(Date, nullable=False)  # day the event pertains to
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # "llm" | "deterministic"
    text: Mapped[str] = mapped_column(Text, nullable=False)  # truncated to 300 chars at write time
    # Normalized text (trim/lower/collapsed whitespace) — the dedup index target, not
    # meant to be read back.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    user: Mapped["User"] = relationship(back_populates="coach_journal_entries")  # noqa: F821
