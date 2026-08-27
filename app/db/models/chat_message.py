import uuid

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class ChatMessage(Base, TimestampMixin):
    """Message du fil de conversation coach ↔ athlète."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        # Present in migrations/init.sql (there, DESC on created_at), absent from this
        # model until spec 003's structural fidelity pass (T024). Kept ascending here:
        # created_at lives on TimestampMixin, so it isn't a direct class attribute at
        # __table_args__ evaluation time to call .desc() on, and a plain ascending
        # composite index is scanned efficiently in either direction by both backends —
        # not worth the added complexity for this query pattern.
        Index("idx_chat_messages_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)   # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)   # "injury_report" | "plan_modification" | "question" | "other"
    tool_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship(back_populates="chat_messages")  # noqa: F821
