"""Persistence for freestyle-mode calendar publications (spec 010).

Deliberately separate from app/db/repositories/publication_repo.py's PublishedEntry: a
freestyle publication belongs to no plan and was never batch-approved, so it gets its own
smaller table (FreestylePublishedEntry) instead of nullable plan_id/approval_id columns
bolted onto the plan-shaped one (see specs/010-publish-freestyle-session/research.md
Decision 4).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.publication import FreestylePublishedEntry


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    external_id: str,
    intervals_event_id: str,
    session_date: date,
    workout_type: str,
    content_hash: str,
) -> FreestylePublishedEntry:
    entry = FreestylePublishedEntry(
        id=uuid.uuid4(),
        user_id=user_id,
        external_id=external_id,
        intervals_event_id=intervals_event_id,
        session_date=session_date,
        workout_type=workout_type,
        content_hash=content_hash,
        published_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_active_for_user(
    session: AsyncSession, user_id: uuid.UUID
) -> list[FreestylePublishedEntry]:
    """Live (non-withdrawn) freestyle publications — what a withdrawal action (manual or
    on a mode switch, FR-011) iterates."""
    result = await session.execute(
        select(FreestylePublishedEntry).where(
            FreestylePublishedEntry.user_id == user_id,
            FreestylePublishedEntry.withdrawn_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def mark_withdrawn(session: AsyncSession, entry_id: uuid.UUID) -> None:
    result = await session.execute(
        select(FreestylePublishedEntry).where(FreestylePublishedEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError(f"No FreestylePublishedEntry {entry_id}")
    entry.withdrawn_at = datetime.now(UTC)
    await session.flush()
