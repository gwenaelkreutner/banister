import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class MealEntry(Base, TimestampMixin):
    """Un repas ou un récap de journée loggé en langage naturel (spec 008).

    `entry_date` est le jour auquel l'entrée compte — pas forcément le jour où elle a été
    loggée (`created_at`, via TimestampMixin) : "hier soir j'ai mangé..." vise hier.
    """

    __tablename__ = "meal_entries"
    __table_args__ = (
        Index("idx_meal_entries_user_date", "user_id", "entry_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "meal" | "day_recap"
    # "breakfast"|"lunch"|"dinner"|"snack"|"other"|None — jamais renseigné pour "day_recap"
    meal_slot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Les mots de l'athlète, tels quels — jamais une paraphrase du LLM (spec 008 research R1)
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_calories: Mapped[int] = mapped_column(nullable=False)

    user: Mapped["User"] = relationship(back_populates="meal_entries")  # noqa: F821
