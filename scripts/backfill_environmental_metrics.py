#!/usr/bin/env python3
"""Rattrapage `elevation_gain_m`/`average_temp_c`/`kilojoules` sur les séances loguées
AVANT que ces champs soient mappés à l'ingestion (2026-09-21) — même situation que
`scripts/backfill_session_metrics.py` (efficiency_factor/hrr/rpe), même version "toutes
les séances d'un coup" du principe.

    python -m scripts.backfill_environmental_metrics            # dry-run (rien n'est écrit)
    python -m scripts.backfill_environmental_metrics --apply     # écrit réellement

Un appel `client.get_activity()` par séance candidate (celles ayant un
`source_activity_id` et au moins un des 3 champs encore NULL), ré-utilise
`mapper.py::map_activity_to_analyzed_session()` (même logique qu'à l'ingestion, pas de
duplication) pour extraire `total_elevation_gain`/`average_temp`/`icu_joules`.

Ne touche jamais un champ déjà renseigné. `average_temp_c` reste `None` après le
rattrapage pour une sortie indoor (VirtualRide, pas de capteur météo côté source) — ce
n'est pas un échec du script, juste l'absence légitime de la donnée.

Poli avec l'API : une pause courte entre deux appels, une activité en échec est
signalée et sautée plutôt que d'interrompre tout le run.
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
from app.db.repositories import session_log_repo, user_repo  # noqa: E402
from app.providers.intervals.client import IntervalsClient  # noqa: E402
from app.providers.intervals.mapper import map_activity_to_analyzed_session  # noqa: E402

_SLEEP_BETWEEN_CALLS_S = 0.3


async def run(*, apply: bool) -> None:
    client = IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )
    async with AsyncSessionFactory() as session:
        user = await user_repo.get_single_user(session)
        if user is None:
            raise SystemExit("No user in the database.")

        logs = await session_log_repo.get_all_for_user(session, user.id)
        candidates = [
            log for log in logs
            if log.source_activity_id
            and (
                log.elevation_gain_m is None
                or log.average_temp_c is None
                or log.kilojoules is None
            )
        ]

        print(f"{len(logs)} séances au total, {len(candidates)} candidates au rattrapage.")
        if not candidates:
            print("Rien à faire.")
            return

        updated = skipped_no_data = failed = 0

        for log in candidates:
            try:
                payload = await client.get_activity(log.source_activity_id)
            except Exception as exc:
                print(f"  ⚠️  {log.logged_date} ({log.source_activity_id}) : échec fetch — {exc}")
                failed += 1
                await asyncio.sleep(_SLEEP_BETWEEN_CALLS_S)
                continue

            analyzed = map_activity_to_analyzed_session(payload)
            changes = []
            if log.elevation_gain_m is None and analyzed.elevation_gain_m is not None:
                changes.append(f"elevation_gain_m={analyzed.elevation_gain_m}")
                if apply:
                    log.elevation_gain_m = analyzed.elevation_gain_m
            if log.average_temp_c is None and analyzed.average_temp_c is not None:
                changes.append(f"average_temp_c={analyzed.average_temp_c:.1f}")
                if apply:
                    log.average_temp_c = analyzed.average_temp_c
            if log.kilojoules is None and analyzed.kilojoules is not None:
                changes.append(f"kilojoules={analyzed.kilojoules:.1f}")
                if apply:
                    log.kilojoules = analyzed.kilojoules

            if changes:
                print(f"  {log.logged_date} ({log.source_activity_id}) : {', '.join(changes)}")
                updated += 1
            else:
                skipped_no_data += 1

            await asyncio.sleep(_SLEEP_BETWEEN_CALLS_S)

        if apply:
            await session.commit()

        print(
            f"\n{updated} séances mises à jour, {skipped_no_data} sans donnée côté source, "
            f"{failed} en échec de récupération."
        )
        if not apply:
            print("Dry-run — rien n'a été écrit. Relance avec --apply pour appliquer.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Écrit réellement les changements (par défaut : dry-run, affiche seulement).",
    )
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
