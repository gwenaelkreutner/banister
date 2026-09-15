"""Selection logic for the evening calorie-tracking reminder (spec 008 US5).

A pure, tiny predicate kept out of `app/main.py` on purpose: that module instantiates a
real `Bot`/`Dispatcher` at import time (`bot = create_bot()`), so importing it from a test
would require real Telegram credentials — no existing test does that (confirmed by grep
before writing this). This module has no such side effect and can be unit tested directly
(research R4/tasks.md T026).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import meal_entry_repo


async def needs_reminder(session: AsyncSession, user_id: uuid.UUID, today: date) -> bool:
    """True if the athlete has logged nothing for `today` yet — the reminder's entire
    selection condition. `meal_entries` having zero rows for today *is* the "not yet
    done" state; no separate "did we already remind" column exists (research R4)."""
    totals = await meal_entry_repo.daily_totals(session, user_id, today, today)
    return not totals
