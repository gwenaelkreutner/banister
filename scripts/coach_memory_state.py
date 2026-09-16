#!/usr/bin/env python3
"""Describe what the coach has durably memorized about the athlete.

    python -m scripts.coach_memory_state --describe

Reads `athlete_profiles.coach_memory` / `.athlete_notes` directly — the same fields
`update_coach_memory` (an LLM tool, see app/llm/tools.py) writes to and
build_system_prompt() reads back into every chat turn. Read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.client import AsyncSessionFactory  # noqa: E402
from app.db.repositories import profile_repo, user_repo  # noqa: E402


async def describe() -> None:
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")
        profile = await profile_repo.get_by_user_id(session, user.id)
        if profile is None:
            raise SystemExit("No athlete profile yet — run /setup first.")

        memory = list(profile.coach_memory or [])
        notes = dict(profile.athlete_notes or {})

    print("Coach memory\n")

    print(f"add_note entries ({len(memory)}):")
    if not memory:
        print("  (none)")
    for entry in memory:
        if isinstance(entry, dict):
            category = entry.get("category", "?")
            note = entry.get("note", entry)
            print(f"  [{category}] {note}")
        else:
            print(f"  {entry}")

    print(f"\nathlete_notes keys ({len(notes)}):")
    if not notes:
        print("  (none)")
    for key, value in notes.items():
        print(f"  {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--describe", action="store_true", help="Print coach_memory and athlete_notes (default)."
    )
    parser.parse_args()
    asyncio.run(describe())


if __name__ == "__main__":
    main()
