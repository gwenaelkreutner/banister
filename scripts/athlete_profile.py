#!/usr/bin/env python3
"""Show what intervals.icu knows about the athlete (spec 007 quickstart Scenario 0).

    python -m scripts.athlete_profile --describe

Prints the confirmation-screen values with their origins — this is the exact input to
first-run setup, so running it tells you whether setup will still need to ask anything.
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

from app.config import settings  # noqa: E402
from app.providers.intervals.athlete_profile import read_athlete_profile  # noqa: E402
from app.providers.intervals.client import IntervalsClient  # noqa: E402


async def describe() -> None:
    client = IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )
    p = await read_athlete_profile(client)

    rows = [
        ("FTP", p.ftp, "W"),
        ("LTHR", p.lthr, "bpm"),
        ("FC max", p.max_hr, "bpm"),
        ("FC repos", p.resting_hr, "bpm"),
        ("Poids", p.weight_kg, "kg"),
        ("Sexe", p.sex, ""),
        ("Âge", p.age, "ans"),
    ]
    print("Profil lu depuis intervals.icu :\n")
    for label, rv, unit in rows:
        if rv.present:
            note = f"  ⚠️ {rv.note}" if rv.note else ""
            print(f"  {label:12} {rv.value} {unit:4} · {rv.origin}{note}")
        else:
            print(f"  {label:12} — absent (setup demandera cette valeur)")
    print(f"\n  Mode de pilotage : {p.coaching_mode}  (capteur de puissance : "
          f"{'oui' if p.has_power_meter else 'non'})")
    print(
        "\nSetup demandera uniquement : objectif, date, volume voulu, contraintes santé."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--describe", action="store_true", help="List the values (default).")
    parser.parse_args()
    asyncio.run(describe())


if __name__ == "__main__":
    main()
