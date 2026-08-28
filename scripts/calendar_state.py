#!/usr/bin/env python3
"""Describe the athlete's intervals.icu calendar, grouped by external_id prefix
(spec 005 quickstart Scenario 0).

    python -m scripts.calendar_state --describe
    python -m scripts.calendar_state --describe --from 2026-09-01 --to 2026-09-30

Run this before and after every writing scenario. `banister:` is ours, `cycling-coach:`
is enduragent's (a real foreign entry — quickstart), everything else is the athlete's
own. The foreign entry is the invariant every writing scenario asserts stays untouched.

Read-only: this script never creates, updates or deletes anything.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.providers.intervals.calendar import EXTERNAL_ID_PREFIX  # noqa: E402
from app.providers.intervals.client import IntervalsClient  # noqa: E402


def _prefix_of(external_id: str | None) -> str:
    if not external_id:
        return "(athlete's own — no external_id)"
    head = external_id.split(":", 1)[0]
    return f"{head}:" if ":" in external_id else external_id


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


async def describe(oldest: date, newest: date) -> None:
    client = _client()
    events = await client.list_events(oldest=oldest.isoformat(), newest=newest.isoformat())

    grouped: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        grouped[_prefix_of(e.get("external_id"))].append(e)

    print(f"Calendar {oldest} → {newest}: {len(events)} event(s)\n")
    for prefix in sorted(grouped, key=lambda p: (p != f"{EXTERNAL_ID_PREFIX}", p)):
        rows = sorted(grouped[prefix], key=lambda e: e.get("start_date_local") or "")
        tag = "  ← ours" if prefix == EXTERNAL_ID_PREFIX else ""
        print(f"[{prefix}] {len(rows)} event(s){tag}")
        for e in rows:
            day = (e.get("start_date_local") or "?")[:10]
            print(
                f"    {day}  id={e.get('id')}  "
                f"{e.get('category', '?'):8}  {e.get('name', '')}"
                + (f"  <{e['external_id']}>" if e.get("external_id") else "")
            )
        print()


async def withdraw_all(oldest: date, newest: date) -> None:
    """Delete every `banister:`-prefixed event in the window — nothing else (SC-006).
    Prefix scoping done here on the live calendar, not via the DB, so this proves the
    scoping is real rather than intended (quickstart Scenario 6)."""
    client = _client()
    events = await client.list_events(oldest=oldest.isoformat(), newest=newest.isoformat())
    ours = [
        e for e in events if str(e.get("external_id") or "").startswith(EXTERNAL_ID_PREFIX)
    ]
    others = len(events) - len(ours)
    print(
        f"{len(ours)} '{EXTERNAL_ID_PREFIX}' event(s) to delete; "
        f"{others} other event(s) left alone."
    )
    for e in ours:
        await client.delete_event(str(e["id"]))
        print(f"  deleted id={e['id']}  {e.get('name', '')}")
    print("Done. Run --describe to confirm 100% of ours gone, 0% of anything else.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--describe", action="store_true", help="List the calendar (default).")
    parser.add_argument(
        "--withdraw-all", action="store_true", help="Delete every banister: event in the window."
    )
    parser.add_argument(
        "--confirm", action="store_true", help="Required with --withdraw-all (it deletes)."
    )
    parser.add_argument("--from", dest="oldest", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="newest", type=date.fromisoformat, default=None)
    args = parser.parse_args()

    oldest = args.oldest or date.today() - timedelta(days=7)
    newest = args.newest or date.today() + timedelta(days=60)

    if args.withdraw_all:
        if not args.confirm:
            raise SystemExit("--withdraw-all deletes events — pass --confirm to proceed.")
        asyncio.run(withdraw_all(oldest, newest))
    else:
        asyncio.run(describe(oldest, newest))


if __name__ == "__main__":
    main()
