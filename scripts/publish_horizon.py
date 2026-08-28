#!/usr/bin/env python3
"""Dev/test utility: run request -> approve -> publish in one call, for the owner's
active plan (spec 005 quickstart Scenario 3).

    python -m scripts.publish_horizon --approve-for-test

This bypasses the Telegram approval UI ONLY — it still goes through
publication.request_publication / authorize_publication / execute_publication, so the
approval gate (FR-001/FR-004) and idempotent diffing (FR-014) are exercised exactly as
in production. Running it five times in a row is quickstart Scenario 3's SC-003 check.

Writes to the real calendar. `--approve-for-test` is required and is the whole safety
interlock — there is no accidental-invocation path.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db.client import AsyncSessionFactory  # noqa: E402
from app.db.repositories import plan_repo, publication_repo, user_repo  # noqa: E402
from app.providers.intervals.client import IntervalsClient  # noqa: E402
from app.services import publication  # noqa: E402


async def run() -> None:
    client = IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database — complete onboarding first.")
        plan = await plan_repo.get_active_plan(session, user.id)
        if plan is None:
            raise SystemExit("No active plan — run /setup first.")

        request = await publication.request_publication(session, user, plan)
        print(request.text)
        print("\n--- auto-approving (test) ---\n")
        await publication_repo.mark_approved(session, request.approval.id)

        report = await publication.execute_publication(
            session, client, user, plan, request.approval
        )
        await session.commit()
        print(report.text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--approve-for-test",
        action="store_true",
        required=True,
        help="Required acknowledgement that this writes to the real calendar.",
    )
    parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
