#!/usr/bin/env python3
"""Run the response verifier over real chat history (spec 006 quickstart Scenario 3).

    python -m scripts.verify_corpus --last 100

For the last N assistant `chat_messages`, prints every `(metric term, number)` claim it
finds and its verdict — so the verifier's *precision* on real prose can be reviewed by
hand, not just its catch rate (spec 006 research R5: a real reply carried fourteen
numerals of which twelve were durations and zone codes).

It has no MetricRegistry for historical messages (the context that produced them is
gone), so `unretrieved` here is expected and not meaningful; what matters is whether the
*claim extraction* fires on things that are not claims. Read the "CLAIMS DETECTED" list
and ask: is each one really a metric citation?

Read-only.
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
from app.db.models.chat_message import ChatMessage  # noqa: E402
from app.services.response_verification import _extract_claims  # noqa: E402


async def run(last: int) -> None:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.role == "assistant")
                .order_by(ChatMessage.created_at.desc())
                .limit(last)
            )
        ).scalars().all()

    total_claims = 0
    messages_with_claims = 0
    print(f"Scanned {len(rows)} assistant message(s).\n")
    for i, row in enumerate(rows, 1):
        claims = _extract_claims(row.content or "")
        if not claims:
            continue
        messages_with_claims += 1
        total_claims += len(claims)
        print(f"── message {i} ──")
        print((row.content or "").strip()[:300])
        print("  CLAIMS DETECTED:")
        for c in claims:
            print(f"    [{c.metric}] {c.stated_text!r}  in: “{c.sentence[:90]}”")
        print()

    print(
        f"\n{total_claims} claim(s) detected across {messages_with_claims} message(s). "
        f"Review each: a duration, a zone, or a cadence appearing here is a false positive "
        f"and the anchoring needs tightening (research R5)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--last", type=int, default=100, help="How many assistant messages.")
    args = parser.parse_args()
    asyncio.run(run(args.last))


if __name__ == "__main__":
    main()
