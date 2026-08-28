#!/usr/bin/env python3
"""Describe what the athlete's data actually supports (spec 006 quickstart Scenario 0).

    python -m scripts.guardrail_state --describe

Per signal: days with data, date range covered, whether a baseline is establishable, and
whether a current observation exists. Run this first — it tells you which quickstart
scenarios can be checked live and which need fixtures (spec 006 research R1 found no HRV,
no sleep, and no resting HR since 2026-07-19 for this athlete).

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
from app.db.repositories import user_repo, wellness_repo  # noqa: E402
from app.engine.baselines import rolling_baseline_stats  # noqa: E402
from app.engine.guardrail_thresholds import (  # noqa: E402
    BASELINE_MIN_SAMPLES,
    BASELINE_WINDOW_DAYS,
)

_SIGNALS = [
    ("ctl", "CTL (charge chronique)"),
    ("atl", "ATL (charge aiguë)"),
    ("ramp_rate", "rampe de charge"),
    ("hrv", "VFC"),
    ("resting_hr", "FC de repos"),
    ("sleep_seconds", "sommeil"),
]


async def describe(today: date) -> None:
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")
        history = await wellness_repo.get_range(
            session, user.id, today - timedelta(days=365), today
        )
        today_row = await wellness_repo.get_by_date(session, user.id, today)

    print(f"Wellness signals as of {today} ({len(history)} day(s) on file)\n")
    for field, label in _SIGNALS:
        readings = [(w.date, getattr(w, field)) for w in history if getattr(w, field) is not None]
        if not readings:
            print(f"  {label:26} — no data at all")
            continue
        first, last = readings[0][0], readings[-1][0]
        past = [(d, float(v)) for d, v in readings if d < today]
        stats = rolling_baseline_stats(
            past, today=today, window_days=BASELINE_WINDOW_DAYS, min_samples=BASELINE_MIN_SAMPLES
        )
        has_today = today_row is not None and getattr(today_row, field) is not None
        baseline = (
            f"baseline OK (μ={stats[0]:.1f}, σ={stats[1]:.1f})"
            if stats is not None
            else f"baseline NOT establishable (<{BASELINE_MIN_SAMPLES} in {BASELINE_WINDOW_DAYS}d)"
        )
        current = "current reading: yes" if has_today else "current reading: NONE"
        stale = (
            "" if (has_today or stats is None)
            else "   ⚠️ baseline w/o current reading — R1 trap"
        )
        print(
            f"  {label:26} {len(readings):3} day(s)  {first:%Y-%m-%d} → {last:%Y-%m-%d}\n"
            f"  {'':26} {baseline}  |  {current}{stale}"
        )
    print(
        "\nGuardrail readiness: workload signals (CTL/ATL/ramp) evaluate whenever a "
        "wellness row exists; recovery signals (VFC/FC repos) need a baseline AND a "
        "reading today."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--describe", action="store_true", help="List signal readiness (default).")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    asyncio.run(describe(args.as_of or date.today()))


if __name__ == "__main__":
    main()
