import uuid

from sqlalchemy import JSON, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin


class AthleteProfile(Base, TimestampMixin):
    __tablename__ = "athlete_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    profile: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Python-side defaults rather than server_default: the ORM always constructs this row
    # (see profile_repo.py), and a server-side JSON default is PostgreSQL-cast syntax that
    # is not portable without dialect-specific branching this project doesn't otherwise need.
    coach_memory: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    athlete_notes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    user: Mapped["User"] = relationship(back_populates="profile")  # noqa: F821
