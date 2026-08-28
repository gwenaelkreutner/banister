"""Persistence for training guardrails (spec 006 data-model.md §Persisted).

Only two things survive a restart, and both are records of something that *happened*
rather than something that *is*: a response check that failed (FR-021, so failure
frequency is measurable for SC-001/SC-002) and a guardrail recommendation the athlete
decided on (FR-025, so the same occurrence is not re-raised). Findings and baselines are
derived on demand, never stored.

Populated per user story: check-failure recording (US3 = T034), acknowledgements
(US4 = T041).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.guardrail import GuardrailAcknowledgement, ResponseCheckFailure


async def record_check_failure(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    failure_kind: str,          # "mismatch" | "unretrieved"
    metric_name: str,
    stated_value: str,
    expected_value: str | None,
    response_excerpt: str,
) -> None:
    """Record one claim that failed verification (FR-021). Kept indefinitely — SC-001
    and SC-002 are measurements over a corpus, and a table that forgets its failures
    cannot support them."""
    session.add(
        ResponseCheckFailure(
            id=uuid.uuid4(),
            user_id=user_id,
            failure_kind=failure_kind,
            metric_name=metric_name,
            stated_value=stated_value[:64],
            expected_value=expected_value[:64] if expected_value is not None else None,
            response_excerpt=response_excerpt,
            occurred_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def count_check_failures(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.execute(
        select(ResponseCheckFailure).where(ResponseCheckFailure.user_id == user_id)
    )
    return len(list(result.scalars().all()))


# ── GuardrailAcknowledgement (US4 — T041) ────────────────────────────────────


async def get_acknowledgement(
    session: AsyncSession, user_id: uuid.UUID, occurrence_key: str
) -> GuardrailAcknowledgement | None:
    result = await session.execute(
        select(GuardrailAcknowledgement).where(
            GuardrailAcknowledgement.user_id == user_id,
            GuardrailAcknowledgement.occurrence_key == occurrence_key,
        )
    )
    return result.scalar_one_or_none()


async def record_acknowledgement(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    finding_kind: str,
    occurrence_key: str,
    decision: str,              # "accepted" | "declined"
) -> None:
    session.add(
        GuardrailAcknowledgement(
            id=uuid.uuid4(),
            user_id=user_id,
            finding_kind=finding_kind,
            occurrence_key=occurrence_key,
            decision=decision,
            decided_at=datetime.now(UTC),
        )
    )
    await session.flush()
