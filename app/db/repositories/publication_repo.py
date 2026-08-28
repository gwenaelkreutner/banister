"""Persistence for calendar publication (spec 005 data-model.md).

Two entities: PublicationApproval (recorded consent, content-bound — FR-004/FR-005) and
PublishedEntry (one written calendar event, traceable to its SessionSpec — FR-013).
Withdrawn entries are kept, never deleted: FR-024 needs "we withdrew this" / "the athlete
deleted this" / "we never published this" all distinguishable.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.publication import PublicationApproval, PublishedEntry

# ── PublicationApproval ──────────────────────────────────────────────────────


async def create_approval(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    content_hash: str,
    horizon_start: date,
    horizon_end: date,
    session_count: int,
) -> PublicationApproval:
    """Record a fresh `pending` approval request."""
    approval = PublicationApproval(
        id=uuid.uuid4(),
        user_id=user_id,
        plan_id=plan_id,
        content_hash=content_hash,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        session_count=session_count,
        status="pending",
        requested_at=datetime.now(UTC),
    )
    session.add(approval)
    await session.flush()
    return approval


async def get_approval(
    session: AsyncSession, approval_id: uuid.UUID
) -> PublicationApproval | None:
    result = await session.execute(
        select(PublicationApproval).where(PublicationApproval.id == approval_id)
    )
    return result.scalar_one_or_none()


async def mark_approved(session: AsyncSession, approval_id: uuid.UUID) -> None:
    """pending -> approved. Terminal."""
    approval = await get_approval(session, approval_id)
    if approval is None:
        raise ValueError(f"No PublicationApproval {approval_id}")
    approval.status = "approved"
    approval.decided_at = datetime.now(UTC)
    await session.flush()


async def mark_declined(session: AsyncSession, approval_id: uuid.UUID) -> None:
    """pending -> declined. Kept, not deleted — FR-003 requires not re-asking unprompted,
    and a deleted record cannot express "they already said no."."""
    approval = await get_approval(session, approval_id)
    if approval is None:
        raise ValueError(f"No PublicationApproval {approval_id}")
    approval.status = "declined"
    approval.decided_at = datetime.now(UTC)
    await session.flush()


# ── PublishedEntry ───────────────────────────────────────────────────────────


async def create_published_entry(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    approval_id: uuid.UUID,
    external_id: str,
    intervals_event_id: str,
    session_date: date,
    week_number: int,
    day_of_week: int,
    content_hash: str,
) -> PublishedEntry:
    entry = PublishedEntry(
        id=uuid.uuid4(),
        user_id=user_id,
        plan_id=plan_id,
        approval_id=approval_id,
        external_id=external_id,
        intervals_event_id=intervals_event_id,
        session_date=session_date,
        week_number=week_number,
        day_of_week=day_of_week,
        content_hash=content_hash,
        published_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def update_published_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    *,
    intervals_event_id: str | None = None,
    content_hash: str | None = None,
    approval_id: uuid.UUID | None = None,
) -> None:
    """Reflect a re-publication onto an existing row (the API has no upsert — R2)."""
    result = await session.execute(
        select(PublishedEntry).where(PublishedEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError(f"No PublishedEntry {entry_id}")
    if intervals_event_id is not None:
        entry.intervals_event_id = intervals_event_id
    if content_hash is not None:
        entry.content_hash = content_hash
    if approval_id is not None:
        entry.approval_id = approval_id
    entry.withdrawn_at = None  # a re-publication revives a previously withdrawn slot
    await session.flush()


async def get_active_entries_for_plan(
    session: AsyncSession, user_id: uuid.UUID, plan_id: uuid.UUID
) -> list[PublishedEntry]:
    """Live (non-withdrawn) entries for a plan — the diff baseline for republication."""
    result = await session.execute(
        select(PublishedEntry).where(
            PublishedEntry.user_id == user_id,
            PublishedEntry.plan_id == plan_id,
            PublishedEntry.withdrawn_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def get_entry_by_external_id(
    session: AsyncSession, user_id: uuid.UUID, external_id: str
) -> PublishedEntry | None:
    result = await session.execute(
        select(PublishedEntry).where(
            PublishedEntry.user_id == user_id,
            PublishedEntry.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def mark_withdrawn(session: AsyncSession, entry_id: uuid.UUID) -> None:
    result = await session.execute(
        select(PublishedEntry).where(PublishedEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise ValueError(f"No PublishedEntry {entry_id}")
    entry.withdrawn_at = datetime.now(UTC)
    await session.flush()
