import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, TimestampMixin
from app.db.types import UtcDateTime


class OAuthConnection(Base, TimestampMixin):
    """Connexion OAuth à un provider externe (Strava, Garmin, etc.).

    NOTE (spec 003): survit intact à ce portage. Table appartenant à l'intégration
    provider que spec 002 supprime — sa suppression appartient à cette spec-là, pas à
    celle-ci (voir plan.md item ouvert 3 / tasks.md T014)."""

    __tablename__ = "oauth_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # "strava", "garmin", …
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped["User"] = relationship(back_populates="oauth_connections")  # noqa: F821
