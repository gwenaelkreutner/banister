"""The five-minute detection loop (spec 002 T032-T036, FR-006..FR-014).

Detection only. This module finds unreported activities and exposes a bounded
announcement policy — it does not send any notification and does not mark anything as
reported itself (FR-012: that has to happen only after real delivery succeeds, which
Phase 6 wires up). Running this against a live account produces no athlete-visible
effect, by design (Phase 5 checkpoint).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import sync_state_repo
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.errors import (
    CredentialRejectedError,
    IntervalsError,
    RateLimitedError,
    TransientError,
)

logger = logging.getLogger(__name__)

# research R4: "a moving window of a few days." Wide enough to catch activities that
# sync or get re-analyzed a day or two late, without re-fetching months of history on
# every tick — list_activities is one request regardless of the window size, so the
# cost of a wider window is negligible next to the benefit of not missing a late sync.
DEFAULT_WINDOW_DAYS = 7

# FR-007/FR-008: protects the source's quota (5,000 req/day, 2,500/15min, 10/sec —
# research R4). At this floor, steady-state list_activities alone is 1,440 req/day
# (~29% of the daily budget), leaving headroom for a detail fetch on each genuinely new
# activity. The operator can configure anything at or above this; nothing lower.
MIN_POLL_INTERVAL_MINUTES = 1

# FR-011: never blast more than this many notifications from one detection pass — every
# detected activity is still returned (still "accounted for"), just not all individually
# announced. The exact number is a product choice with no functional consequence here
# (Phase 5 doesn't send anything yet); it exists so Phase 6's delivery code has a policy
# to call rather than inventing one under time pressure.
DEFAULT_MAX_ANNOUNCEMENTS_PER_CYCLE = 3

_RETRY_DELAYS_S = (5, 15, 45)  # FR-013: bounded retries, not a retry storm


def resolve_poll_interval_minutes() -> int:
    """FR-007's configurable interval, clamped against FR-008's protective floor rather
    than refusing to start over it — a bad value here is a nuisance, not a data-safety
    issue, so degrade gracefully instead of crashing startup (contrast with FR-003's
    credential check, which does refuse to start)."""
    configured = settings.intervals_poll_interval_minutes
    if configured < MIN_POLL_INTERVAL_MINUTES:
        logger.warning(
            "INTERVALS_POLL_INTERVAL_MINUTES=%s is below the protective floor of %s "
            "minutes — using %s instead.",
            configured,
            MIN_POLL_INTERVAL_MINUTES,
            MIN_POLL_INTERVAL_MINUTES,
        )
        return MIN_POLL_INTERVAL_MINUTES
    return configured


async def detect_new_activities(
    session: AsyncSession,
    user_id: uuid.UUID,
    client: IntervalsClient,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> list[dict]:
    """Activities in the trailing window that have no reported marker yet. An edited or
    renamed activity keeps its id (research R4), so it is filtered out here exactly like
    any other already-reported activity — satisfying FR-010 without any special case."""
    today = today or date.today()
    oldest = today - timedelta(days=window_days)

    activities = await client.list_activities(oldest=oldest.isoformat(), newest=today.isoformat())

    unreported = []
    for activity in activities:
        activity_id = str(activity["id"])
        if not await sync_state_repo.is_reported(session, user_id, activity_id):
            unreported.append(activity)

    unreported.sort(key=lambda a: a.get("start_date") or "")
    return unreported


def bound_announcements(
    activities: list[dict],
    *,
    max_announcements: int = DEFAULT_MAX_ANNOUNCEMENTS_PER_CYCLE,
) -> tuple[list[dict], list[dict]]:
    """FR-011. Oldest-first: after a gap, the earliest missed ride is the one that gets
    announced, not whichever happens to sort last. Returns (to_announce,
    ingest_only) — the second list is not discarded, only not individually announced;
    the caller still owes them their contribution to CTL/ATL history."""
    if len(activities) <= max_announcements:
        return activities, []
    return activities[:max_announcements], activities[max_announcements:]


_poll_lock = asyncio.Lock()


async def poll_once(
    session: AsyncSession, user_id: uuid.UUID, client: IntervalsClient
) -> list[dict]:
    """One detection pass, guarded against overlapping with another in-flight pass
    (FR-014) and retried against transient failures without alerting on every attempt
    (FR-013). Returns the unreported activities found, or an empty list if every retry
    was exhausted (logged, not raised — a poll tick failing must not crash the
    scheduler loop)."""
    if _poll_lock.locked():
        logger.info("Skipping this poll tick — a previous one is still in flight.")
        return []

    async with _poll_lock:
        last_error: Exception | None = None
        for attempt, delay in enumerate((0, *_RETRY_DELAYS_S)):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await detect_new_activities(session, user_id, client)
            except CredentialRejectedError:
                # Not transient — retrying wastes quota and never succeeds. Logged at
                # error level; surfacing this to the athlete is Phase 6's job (the
                # delivery path exists there, not here).
                logger.error(
                    "intervals.icu credential rejected during polling — the athlete "
                    "needs to reconnect."
                )
                raise
            except RateLimitedError as exc:
                last_error = exc
                wait_s = exc.retry_after_seconds or 60
                logger.warning("Rate limited polling intervals.icu — waiting %.0fs.", wait_s)
                await asyncio.sleep(wait_s)
            except TransientError as exc:
                last_error = exc
                logger.warning(
                    "Transient error polling intervals.icu (attempt %s): %s", attempt + 1, exc
                )

        logger.error(
            "Giving up on this poll tick after %s attempts: %s",
            len(_RETRY_DELAYS_S) + 1,
            last_error,
        )
        return []


async def run_poller_scheduler(session_factory, client_factory) -> None:
    """T037's counterpart to app/main.py's other _*_scheduler functions — kept here
    rather than in main.py because the loop itself is provider machinery (plan.md's
    structure decision), not a notification scheduler. `session_factory` and
    `client_factory` are injected rather than imported directly so this loop stays
    testable without a real DB engine or a real API key.
    """
    from app.db.repositories.user_repo import get_single_user

    while True:
        interval_minutes = resolve_poll_interval_minutes()
        await asyncio.sleep(interval_minutes * 60)

        try:
            async with session_factory() as session:
                user = await get_single_user(session)
                if user is None:
                    continue

                client = client_factory()
                unreported = await poll_once(session, user.id, client)
                if unreported:
                    to_announce, ingest_only = bound_announcements(unreported)
                    logger.info(
                        "Poll detected %s unreported activity(ies): %s to announce, "
                        "%s ingest-only this cycle. Not yet wired to notify (Phase 6).",
                        len(unreported),
                        len(to_announce),
                        len(ingest_only),
                    )
        except IntervalsError:
            # Already logged with detail inside poll_once/detect_new_activities' callers
            # (or is the credential-rejected case, which needs no further noise here).
            pass
        except Exception:
            logger.exception("Unexpected error in the intervals.icu poller loop")
