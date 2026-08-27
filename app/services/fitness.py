"""Current fitness (CTL/ATL/TSB), consumed from the source rather than recomputed
(spec 002 FR-016 — the rule this module actually enforces; the mapper's docstring flagged
this as not-yet-done, this closes it).

`app/engine/atl_ctl.py::compute_fitness_from_any()` still exists and is still correct —
it is what seeds a brand-new athlete's very first figure, before any wellness row has ever
been ingested, and what `project_fitness_from_plan()` uses for its theoretical projection
(a local simulation the source cannot provide by definition). What moves here is only the
day-to-day *current* figure shown in /forme, /recap, the chat context, the post-activity
message, and the KPI overtraining check — all of which have a real, dated, source-computed
answer once wellness has been ingested at least once.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import wellness_repo
from app.engine.atl_ctl import FitnessMetrics


@dataclass
class CurrentFitness:
    metrics: FitnessMetrics
    as_of: date       # the wellness row's own date — may be older than today
    is_stale: bool     # True if as_of != the date this was requested for


async def get_current_fitness(
    session: AsyncSession, user_id: uuid.UUID, today: date | None = None
) -> CurrentFitness | None:
    """The athlete's current CTL/ATL/TSB as intervals.icu computed it, or None if no
    wellness row has been ingested yet (brand-new athlete, before the first poll/import
    cycle) — callers fall back to the local estimate in that case, same as before this
    module existed."""
    today = today or date.today()
    row = await wellness_repo.get_latest(session, user_id, on_or_before=today)
    if row is None:
        return None

    return CurrentFitness(
        metrics=FitnessMetrics(atl=row.atl, ctl=row.ctl, tsb=round(row.ctl - row.atl, 1)),
        as_of=row.date,
        is_stale=row.date != today,
    )
