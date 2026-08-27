"""Portable column types for the storage-engine migration (spec 003)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A timezone-aware datetime that survives a round trip through backends with no native
    timezone-aware type.

    SQLite discards the UTC offset on `DateTime(timezone=True)`: a value written with an
    offset comes back naive. Daily boundaries computed from a naive value silently mean a
    different instant than the one that was stored, which is exactly the failure mode
    Principle IV (spec 003 FR-010) exists to prevent.

    This type normalises every value to UTC on write and re-attaches UTC on read, and
    refuses a naive datetime on write rather than guessing which zone it is in.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "UtcDateTime received a naive datetime; attach a timezone before storing "
                "it rather than letting the column guess which zone it is in."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite has no native timezone-aware type: everything it returns is naive.
            # It was normalised to UTC on the way in, so UTC is the correct zone to
            # re-attach here, not a guess.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
