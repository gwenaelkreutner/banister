"""The athlete's feeling about a ride: recorded here, sent back to the data source."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User

logger = logging.getLogger(__name__)


class RideNotFound(LookupError):
    pass


def parse_rpe(token: str) -> tuple[bool, float | None]:
    """`(valid, rpe)`. "skip" is valid and means no feeling given."""
    if token == "skip":
        return True, None
    try:
        value = int(token)
    except ValueError:
        return False, None
    return (True, float(value)) if 1 <= value <= 10 else (False, None)


async def sync_rpe_to_source(client, activity_id: str, rpe: float) -> bool:
    """Return whether the source activity accepted the RPE."""
    try:
        await client.update_activity_rpe(activity_id, int(rpe))
    except Exception:
        logger.exception("Impossible de synchroniser le RPE vers intervals.icu")
        return False
    return True


async def record_rpe(
    session: AsyncSession, user: User, log_id: uuid.UUID, rpe: float | None, client
) -> tuple[SessionLog, bool]:
    """`(log, synced)`. A skipped feeling writes nothing, and counts as nothing to sync."""
    log = await repo.session_log_repo.get_by_id(session, log_id)
    if log is None or log.user_id != user.id:
        raise RideNotFound(str(log_id))
    synced = True
    if rpe is not None:
        log.rpe = rpe
        await session.flush()
        if log.source_activity_id:
            synced = await sync_rpe_to_source(client, log.source_activity_id, rpe)
    return log, synced
