"""Persistence for sessions proposed through the agent API (`SessionProposal`)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.publication import SessionProposal


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_date: date,
    workout_type: str,
    name: str,
    steps: list,
    duration_minutes: int,
    tss_estimate: float,
    content_hash: str,
) -> SessionProposal:
    proposal = SessionProposal(
        id=uuid.uuid4().hex[:12],
        user_id=user_id,
        session_date=session_date,
        workout_type=workout_type,
        name=name,
        steps=steps,
        duration_minutes=duration_minutes,
        tss_estimate=tss_estimate,
        content_hash=content_hash,
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(proposal)
    await session.flush()
    return proposal


async def get(
    session: AsyncSession, user_id: uuid.UUID, proposal_id: str
) -> SessionProposal | None:
    result = await session.execute(
        select(SessionProposal).where(
            SessionProposal.id == proposal_id, SessionProposal.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def claim(session: AsyncSession, proposal_id: str) -> bool:
    """Atomically move pending -> publishing: of two taps on a button, only one wins."""
    result = await session.execute(
        update(SessionProposal)
        .where(SessionProposal.id == proposal_id, SessionProposal.status == "pending")
        .values(status="publishing")
    )
    return result.rowcount == 1


async def set_status(
    session: AsyncSession,
    proposal: SessionProposal,
    status: str,
    *,
    publication_id: uuid.UUID | None = None,
) -> None:
    proposal.status = status
    proposal.publication_id = publication_id
    proposal.decided_at = None if status == "pending" else datetime.now(UTC)
    await session.flush()
