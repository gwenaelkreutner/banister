"""Persistence for the dated coach journal (Enduragent parity review, 2026-09-20).

One write function (`create`, idempotent by content) and one read function (`query`,
the backing of the `memory_query` LLM tool — app/llm/tools.py / app/llm/chat.py). No
generic CRUD abstraction, mirrors the size of `guardrail_repo.py`/`meal_entry_repo.py`.

Findings/coach_memory itself are NOT duplicated here on every turn — only two write
paths ever call `create()`: `_tool_update_coach_memory`'s `add_note` action (source="llm")
and a handful of deterministic hooks (goal change, freestyle toggle, injury report,
source="deterministic"). Routine training data (sessions, RPE, guardrail decisions) is
deliberately never journaled — already structured elsewhere, and Enduragent's own
ledger_append tool description states the same exclusion.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.coach_journal import CoachJournalEntry
from app.db.upsert import dialect_insert

_TEXT_MAX_CHARS = 300
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_dedup(text: str) -> str:
    """Trim/lowercase/collapse whitespace — the same normalization Enduragent's
    event-ledger content digest applies before comparing two events, adapted here to a
    plain indexed column instead of an in-memory hash (this project has no in-memory
    ledger to scan; the DB unique index does the comparison)."""
    return _WHITESPACE_RE.sub(" ", text.strip().lower())


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    entry_date: date,
    category: str,
    source: str,
    text: str,
) -> bool:
    """Insert one dated entry. Returns `True` if a new row was written, `False` if an
    identical (user, date, category, normalized text) entry already existed — a retried
    tool call or a hook firing twice writes the fact once, same as
    `activity_repo.bulk_insert()`'s `on_conflict_do_nothing` pattern."""
    truncated = text[:_TEXT_MAX_CHARS]
    dedup_key = _normalize_for_dedup(truncated)[:200]

    stmt = dialect_insert(session)(CoachJournalEntry).values(
        id=uuid.uuid4(),
        user_id=user_id,
        entry_date=entry_date,
        category=category,
        source=source,
        text=truncated,
        dedup_key=dedup_key,
        occurred_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "entry_date", "category", "dedup_key"],
    )
    result = await session.execute(stmt)
    await session.flush()
    return bool(result.rowcount)


async def query(
    session: AsyncSession,
    user_id: uuid.UUID,
    start_date: date,
    end_date: date,
    *,
    keyword: str | None = None,
    category: str | None = None,
    limit: int = 30,
) -> list[CoachJournalEntry]:
    """Entries in [start_date, end_date], most recent first. `keyword` is a
    case-insensitive substring match on `text` — the same "omit for everything" contract
    as Enduragent's `memory_query`. Caller (app/llm/chat.py) is responsible for the
    truncation notice when `len(result) == limit` might mean more rows exist."""
    stmt = (
        select(CoachJournalEntry)
        .where(
            CoachJournalEntry.user_id == user_id,
            CoachJournalEntry.entry_date >= start_date,
            CoachJournalEntry.entry_date <= end_date,
        )
        .order_by(CoachJournalEntry.entry_date.desc(), CoachJournalEntry.occurred_at.desc())
        .limit(limit)
    )
    if category:
        stmt = stmt.where(CoachJournalEntry.category == category)
    if keyword:
        stmt = stmt.where(CoachJournalEntry.text.ilike(f"%{keyword}%"))

    result = await session.execute(stmt)
    return list(result.scalars().all())
