#!/usr/bin/env python3
"""Describe today's logged meals and running total (spec 008 quickstart Scenario 0).

    python -m scripts.nutrition_state --describe

Reads through `meal_entry_repo.daily_totals` — the same call path the LLM tools use — so
this doubles as a manual sanity check independent of Telegram. Read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.client import AsyncSessionFactory  # noqa: E402
from app.db.repositories import meal_entry_repo, user_repo  # noqa: E402


async def describe(today: date, history_days: int) -> None:
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")
        totals = await meal_entry_repo.daily_totals(
            session, user.id, today - timedelta(days=history_days - 1), today
        )

    by_date = {t.entry_date: t for t in totals}
    print(f"Calorie tracking as of {today}\n")
    for offset in range(history_days - 1, -1, -1):
        d = today - timedelta(days=offset)
        t = by_date.get(d)
        marker = " (aujourd'hui)" if d == today else ""
        if t is None:
            print(f"  {d} — rien loggé{marker}")
        else:
            print(
                f"  {d} — {t.total_calories} kcal estimées ({t.entry_count} entrée(s)){marker}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--describe", action="store_true", help="List recent daily totals (default)."
    )
    parser.add_argument("--days", type=int, default=7, help="How many days back to show.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    asyncio.run(describe(args.as_of or date.today(), args.days))


if __name__ == "__main__":
    main()
