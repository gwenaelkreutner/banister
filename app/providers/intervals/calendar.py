"""Publish structured sessions to the intervals.icu events API, scoped to our own
`banister:` prefix (spec 005 US1 = T014-T015).

All network I/O and ordering live here; the pure DSL text transform is workout_dsl.py.
This module does not touch the database — approval and record-keeping are
provider-agnostic and live in app/services/publication.py (plan.md Constitution Check
III). It returns per-session outcomes; the caller persists and reports them.

Every future read-for-diff / update / delete (US3, US4) filters on
`external_id.startswith(EXTERNAL_ID_PREFIX)`, which is what makes FR-015 ("never modify
entries it did not create") true by construction rather than by care.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from app.engine.schemas import SessionSpec, TrainingPlanSchema
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.workout_dsl import EmptySessionError, render_dsl

EXTERNAL_ID_PREFIX = "banister:"


def build_external_id(
    plan_id: uuid.UUID,
    session_date: date,
    workout_type: str,
    week: int,
    dow: int,
) -> str:
    """`banister:<plan_id_short>:<yyyy-MM-dd>:<workout_type>-<week>-<dow>` (data-model.md
    §External id). The plan id scopes entries to the plan that produced them, so a
    wholesale regeneration leaves the previous plan's entries identifiable and
    withdrawable; date+week+dow makes the entry traceable to its exact SessionSpec (FR-013).
    """
    plan_short = str(plan_id).split("-", 1)[0]
    slug = f"{workout_type}-{week}-{dow}"
    return f"{EXTERNAL_ID_PREFIX}{plan_short}:{session_date.isoformat()}:{slug}"


def build_event_payload(
    session_date: date, name: str, external_id: str, rendered_dsl: str
) -> dict:
    """The event body (contracts/calendar-publication.md §2). Never sends `workout_doc`,
    `moving_time` or `icu_training_load` — intervals.icu derives all three from the DSL
    (research R3/R4)."""
    return {
        "start_date_local": f"{session_date.isoformat()}T00:00:00",
        "category": "WORKOUT",
        "type": "Ride",
        "name": name,
        "external_id": external_id,
        "description": rendered_dsl,
    }


@dataclass
class SessionOutcome:
    """What happened to one planned session in a publication run."""

    session_date: date
    week_number: int
    day_of_week: int
    workout_type: str
    name: str
    external_id: str
    status: str  # "created" | "refused" | "failed"
    intervals_event_id: str | None = None
    rendered_dsl: str | None = None
    detail: str | None = None  # why, for "refused" / "failed"


@dataclass
class PlannedSession:
    session_date: date
    week_number: int
    day_of_week: int
    spec: SessionSpec


def iter_horizon_sessions(
    plan: TrainingPlanSchema, horizon_start: date, horizon_end: date
) -> list[PlannedSession]:
    """Every session whose planned date falls in [horizon_start, horizon_end], ordered
    by date. A week with no `start_date` is skipped rather than guessed."""
    out: list[PlannedSession] = []
    for week in plan.weeks:
        if week.start_date is None:
            continue
        for spec in week.sessions:
            session_date = week.start_date + timedelta(days=spec.day_of_week)
            if horizon_start <= session_date <= horizon_end:
                out.append(
                    PlannedSession(session_date, week.week_number, spec.day_of_week, spec)
                )
    out.sort(key=lambda p: p.session_date)
    return out


async def publish_sessions(
    client: IntervalsClient,
    plan: TrainingPlanSchema,
    plan_id: uuid.UUID,
    horizon_start: date,
    horizon_end: date,
) -> list[SessionOutcome]:
    """Render each in-horizon session's DSL, build its event, and `create_event()` it.

    A session without steps is collected as a refusal (FR-009), never raised past the
    batch. A per-session API failure is collected as "failed" and does not abandon the
    rest (FR-028) — full transient-failure coherence is Phase 8's T050.

    Create-only for now: idempotent diffing (read the window, PUT what exists) lands in
    US3 (T030). Publishing twice here would duplicate — that is the naive implementation
    SC-003 is written to catch.
    """
    outcomes: list[SessionOutcome] = []
    for planned in iter_horizon_sessions(plan, horizon_start, horizon_end):
        spec = planned.spec
        external_id = build_external_id(
            plan_id,
            planned.session_date,
            spec.workout_type,
            planned.week_number,
            planned.day_of_week,
        )
        name = spec.description_fr or spec.workout_type

        if spec.steps is None:
            outcomes.append(
                SessionOutcome(
                    session_date=planned.session_date,
                    week_number=planned.week_number,
                    day_of_week=planned.day_of_week,
                    workout_type=spec.workout_type,
                    name=name,
                    external_id=external_id,
                    status="refused",
                    detail="séance sans structure (plan pré-004) — non publiable",
                )
            )
            continue

        try:
            rendered = render_dsl(spec.steps, plan.zones)
        except EmptySessionError as exc:
            outcomes.append(
                SessionOutcome(
                    session_date=planned.session_date,
                    week_number=planned.week_number,
                    day_of_week=planned.day_of_week,
                    workout_type=spec.workout_type,
                    name=name,
                    external_id=external_id,
                    status="refused",
                    detail=str(exc),
                )
            )
            continue

        payload = build_event_payload(planned.session_date, name, external_id, rendered)

        try:
            event = await client.create_event(payload)
        except Exception as exc:  # noqa: BLE001 — one bad session must not sink the batch (FR-028)
            outcomes.append(
                SessionOutcome(
                    session_date=planned.session_date,
                    week_number=planned.week_number,
                    day_of_week=planned.day_of_week,
                    workout_type=spec.workout_type,
                    name=name,
                    external_id=external_id,
                    status="failed",
                    rendered_dsl=rendered,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        outcomes.append(
            SessionOutcome(
                session_date=planned.session_date,
                week_number=planned.week_number,
                day_of_week=planned.day_of_week,
                workout_type=spec.workout_type,
                name=name,
                external_id=external_id,
                status="created",
                intervals_event_id=str(event.get("id")),
                rendered_dsl=rendered,
            )
        )
    return outcomes
