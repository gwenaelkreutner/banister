from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UtcDateTime


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    # func.now() is portable as-is: SQLite's CURRENT_TIMESTAMP returns UTC (naive text),
    # PostgreSQL's now() returns UTC-aware — UtcDateTime.process_result_value re-attaches
    # UTC to a naive read-back, so both backends yield the same instant. Verified empirically
    # rather than assumed (spec 003 research R2).
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now(), onupdate=func.now()
    )
