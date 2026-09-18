#!/usr/bin/env python3
"""Describe recent LLM token usage from the coaching chat (cost visibility, found
2026-09-18 — the API returns token counts on every call but nothing kept them before
this: app/db/repositories/chat_repo.py::token_usage_by_day()).

    python -m scripts.token_usage_state --describe

Only the agentic chat loop (app/llm/chat_client.py::run_agentic_loop, used by every free
chat message) is measured — one-shot generative calls (plan narrative, activity feedback)
go through a different, thinner provider interface (app/llm/providers/*.py) that doesn't
return usage to its caller. That is most of the system prompt cost anyway: the chat loop
rebuilds the full system prompt (persona, plan, fitness, guardrails, memory) on every
single message.

Read-only.
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
from app.db.repositories import chat_repo, user_repo  # noqa: E402


async def describe(today: date, history_days: int) -> None:
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")
        start = today - timedelta(days=history_days - 1)
        by_day = await chat_repo.token_usage_by_day(session, user.id, start, today)

    print(f"Token usage (chat agentique) — {start} → {today}\n")
    if not by_day:
        print(
            "  Rien à afficher. Soit aucun message dans la fenêtre, soit tous datent "
            "d'avant l'ajout de cette mesure (2026-09-18)."
        )
        return

    total_in = total_out = total_turns = 0
    for row in by_day:
        marker = " (aujourd'hui)" if row.day == today else ""
        print(
            f"  {row.day} — {row.turns} tour(s) | "
            f"{row.tokens_input:>6,} in / {row.tokens_output:>6,} out{marker}"
        )
        total_in += row.tokens_input
        total_out += row.tokens_output
        total_turns += row.turns

    avg_in = total_in / total_turns if total_turns else 0
    avg_out = total_out / total_turns if total_turns else 0
    print(
        f"\nTotal : {total_turns} tour(s) | {total_in:,} in / {total_out:,} out\n"
        f"Moyenne par tour : {avg_in:,.0f} in / {avg_out:,.0f} out"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--describe", action="store_true", help="List recent daily token usage (default)."
    )
    parser.add_argument("--days", type=int, default=14, help="How many days back to show.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    asyncio.run(describe(args.as_of or date.today(), args.days))


if __name__ == "__main__":
    main()
