"""Guardrail persistence (spec 006 data-model.md §Persisted).

Two tables, both records of something that happened rather than something that is:

- `ResponseCheckFailure` — a coaching response stated a metric value that did not match
  what was retrieved, or stated a value for a metric never retrieved (FR-021). Kept
  indefinitely and never pruned by this feature: SC-001 and SC-002 are measurements over
  a corpus, and a table that silently forgets its failures cannot support them.
- `GuardrailAcknowledgement` — the athlete was shown a finding and decided on it
  (FR-025). Needed because "the same occurrence" is not answerable from derived state,
  and FR-025 forbids re-raising it while FR-026 forbids silencing the signal.

Findings and baselines are NOT stored — a stored finding is a cache that goes stale the
moment the day it describes passes.
"""
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base
from app.db.types import UtcDateTime


class ResponseCheckFailure(Base):
    """One claim in a coaching response that failed verification (FR-019, FR-021)."""

    __tablename__ = "response_check_failures"
    __table_args__ = (
        Index("idx_response_check_failures_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # "mismatch"  — stated value disagrees with the retrieved value
    # "unretrieved" — a value was stated for a metric that was never retrieved (FR-018)
    failure_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Text, not numeric — the point is to keep the claim exactly as written.
    stated_value: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The sentence containing the claim, so a failure is diagnosable without replaying
    # the whole conversation.
    response_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class GuardrailAcknowledgement(Base):
    """The record that the athlete was shown a guardrail finding and accepted or declined
    it (FR-025, FR-026)."""

    __tablename__ = "guardrail_acknowledgements"
    __table_args__ = (
        Index(
            "idx_guardrail_ack_user_occurrence",
            "user_id",
            "occurrence_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    finding_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # What makes this the *same* occurrence: kind + the day the finding is about
    # (data-model.md). Coarser and one decline silences the guardrail forever (FR-026
    # forbids); finer and every re-evaluation is a new occurrence and the athlete is
    # nagged (FR-025 forbids).
    occurrence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # "accepted" | "declined"
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
