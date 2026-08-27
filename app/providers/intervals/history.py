"""First-connection history import (spec 002 T038-T041, FR-026..029).

Verified against the live account (spec 002 T043's check doubled as verification here):
`list_activities` returns the full payload — the same 183 fields `get_activity` would —
in a single request regardless of window size (73 activities across 120 days, one call,
no pagination). That removes the multi-request/pagination complexity a paginated import
would need, and with it most of the ways an import could be interrupted partway through:
either the one request (+ one bulk insert) succeeds, or nothing was written at all.

Resumability (FR-027) therefore reduces to idempotence: `sync_state.history_import_complete`
gates whether this runs at all, and activity_repo.bulk_insert's on_conflict_do_nothing
means re-running an interrupted attempt (complete still False) can never duplicate a row
already written by a previous partial attempt.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import activity_repo, sync_state_repo
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.wellness import ingest_wellness

# Matches app/engine/atl_ctl.py's own 2xτ_CTL (84j) threshold for when the EMA needs a
# seeded starting CTL rather than being trusted to bootstrap from zero (FR-026: "enough
# to establish a meaningful chronic training load" — this project already has a concrete
# definition of "meaningful" for exactly this purpose).
TARGET_HISTORY_DAYS = 84


@dataclass
class HistoryImportResult:
    activity_count: int
    inserted_count: int
    complete: bool
    little_or_no_history: bool  # FR-029: signals the caller should disclose this


def _to_activity_row(payload: dict) -> dict:
    start = payload.get("start_date_local") or payload.get("start_date") or ""
    activity_date = datetime.fromisoformat(start.replace("Z", "+00:00")).date()
    icu_type = payload.get("type")
    icu_joules = payload.get("icu_joules")

    return {
        "source": "intervals_icu",
        "source_activity_id": str(payload["id"]),
        "activity_date": activity_date,
        "duration_seconds": payload.get("moving_time") or payload.get("elapsed_time"),
        "sport_type": icu_type,
        "environment": "indoor" if icu_type == "VirtualRide" else "outdoor",
        "distance_meters": payload.get("distance"),
        "elevation_gain_meters": payload.get("total_elevation_gain"),
        "avg_watts": payload.get("icu_average_watts"),
        "normalized_watts": payload.get("icu_weighted_avg_watts"),
        "device_watts": bool(payload.get("device_watts")),
        "avg_heartrate": payload.get("average_heartrate"),
        "max_heartrate": payload.get("max_heartrate"),
        "suffer_score": None,  # a legacy field from the previous provider; intervals.icu has no equivalent
        "kilojoules": (icu_joules / 1000) if icu_joules is not None else None,
        "tss": payload.get("icu_training_load"),  # stays None, not 0.0, when absent (FR-020)
        "tss_method": "source" if payload.get("icu_training_load") is not None else None,
        # Which FTP applies is genuinely ambiguous per-athlete (research R9e: icu_ftp,
        # icu_pm_ftp and icu_rolling_ftp disagree) — recording a guess here would be
        # exactly the silent substitution FR-020 forbids, so this stays unset.
        "ftp_used": None,
    }


async def import_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    client: IntervalsClient,
    *,
    target_days: int = TARGET_HISTORY_DAYS,
    today: date | None = None,
) -> HistoryImportResult:
    """Runs once per athlete — a no-op on every call after the first successful one.
    FR-028: check `history_import_complete` on the returned SyncState (via
    sync_state_repo) before presenting any fitness figure derived from this data as
    final; this function itself has no notion of "presenting" anything.
    """
    state = await sync_state_repo.get_or_create_sync_state(session, user_id)
    if state.history_import_complete:
        return HistoryImportResult(0, 0, True, False)

    today = today or date.today()
    oldest = today - timedelta(days=target_days)

    activities = await client.list_activities(oldest=oldest.isoformat(), newest=today.isoformat())
    rows = [_to_activity_row(a) for a in activities]
    inserted = await activity_repo.bulk_insert(session, user_id, rows)

    # Wellness carries a daily CTL/ATL independent of whether an activity happened that
    # day (spec 002 T072-follow-up) — ingested here too, not just going forward from the
    # poller, so a freshly onboarded athlete's /forme reflects the source's real history
    # immediately rather than only from the day they connect onward.
    await ingest_wellness(
        session, user_id, client, oldest=oldest.isoformat(), newest=today.isoformat()
    )

    await sync_state_repo.advance_history_import_cursor(session, user_id, oldest)
    await sync_state_repo.mark_history_import_complete(session, user_id)

    # FR-029: "little" is judged against the same 84-day target this import aims for —
    # fewer than roughly one activity a week over that window is not enough to establish
    # a meaningful chronic load, and the caller needs to know to disclose that rather
    # than present the resulting CTL as if it were well-founded.
    little_history = len(activities) < (target_days // 7)

    return HistoryImportResult(
        activity_count=len(activities),
        inserted_count=inserted,
        complete=True,
        little_or_no_history=little_history,
    )
