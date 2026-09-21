#!/usr/bin/env python3
"""Rattrapage `efficiency_factor`/`hrr`/`rpe` sur les séances loguées AVANT que ces
champs soient mappés à l'ingestion (2026-09-21, commits `56096d2`/`8625e1c`).

    python -m scripts.backfill_session_metrics            # dry-run (rien n'est écrit)
    python -m scripts.backfill_session_metrics --apply     # écrit réellement

Un appel `client.get_activity()` par séance candidate (celles ayant un
`source_activity_id` et au moins un des 3 champs encore NULL), ré-utilise
`mapper.py::map_activity_to_analyzed_session()` (même logique qu'à l'ingestion, pas de
duplication) pour extraire `icu_efficiency_factor`/`icu_hrr`/`icu_rpe`.

Ne touche JAMAIS un champ déjà renseigné — en particulier `rpe`, où le clavier Telegram
reste toujours prioritaire sur la source (même règle que
`app/bot/routers/review.py::_backfill_rpe_from_source`, dont ce script est la version
"toutes les séances d'un coup" plutôt que "à la demande, une par une via /review").

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
            and (log.efficiency_factor is None or log.hrr is None or log.rpe is None)
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
            if log.efficiency_factor is None and analyzed.efficiency_factor is not None:
                changes.append(f"efficiency_factor={analyzed.efficiency_factor}")
                if apply:
                    log.efficiency_factor = analyzed.efficiency_factor
            if log.hrr is None and analyzed.hrr is not None:
                changes.append(f"hrr={analyzed.hrr}")
                if apply:
                    log.hrr = analyzed.hrr
            if log.rpe is None and analyzed.rpe is not None:
                changes.append(f"rpe={analyzed.rpe}")
                if apply:
                    log.rpe = analyzed.rpe

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
