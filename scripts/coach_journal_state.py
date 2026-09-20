#!/usr/bin/env python3
"""Describe the dated coach journal (Enduragent parity review, 2026-09-20).

    python -m scripts.coach_journal_state --describe

Reads `coach_journal_entries` directly — the same table the `memory_query` LLM tool
(app/llm/tools.py / app/llm/chat.py::_tool_memory_query) searches on demand, and that
`update_coach_memory`'s `add_note` action (source="llm") plus a handful of deterministic
hooks (goal change, freestyle toggle, injury report — source="deterministic") write into.
Read-only, mirrors scripts/coach_memory_state.py.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.client import AsyncSessionFactory  # noqa: E402
from app.db.models.coach_journal import CoachJournalEntry  # noqa: E402
from app.db.repositories import user_repo  # noqa: E402


async def describe() -> None:
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")

        result = await session.execute(
            select(CoachJournalEntry)
            .where(CoachJournalEntry.user_id == user.id)
            .order_by(CoachJournalEntry.entry_date.desc(), CoachJournalEntry.occurred_at.desc())
        )
        entries = list(result.scalars().all())

    print(f"Coach journal ({len(entries)} entries)\n")
    if not entries:
        print("  (none)")
        return

    for entry in entries:
        print(f"  {entry.entry_date} [{entry.category}/{entry.source}] {entry.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--describe", action="store_true", help="Print every dated journal entry (default)."
    )
    parser.parse_args()
    asyncio.run(describe())


if __name__ == "__main__":
    main()
