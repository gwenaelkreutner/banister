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
from app.providers.intervals.workout_dsl import (
    EmptySessionError,
    hash_session_content,
    render_dsl,
)

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
    # "created" | "updated" | "unchanged" | "conflict" | "refused" | "failed"
    # "conflict" = the athlete edited or deleted our entry — surfaced, never overwritten
    #              or recreated (FR-023, FR-024).
    status: str
    intervals_event_id: str | None = None
    content_hash: str | None = None
    rendered_dsl: str | None = None
    detail: str | None = None  # why, for "conflict" / "refused" / "failed"


@dataclass
class KnownEntry:
    """What the service layer already knows about a previously published slot — enough
    for publish_sessions to decide skip vs update without importing the DB (layering)."""

    intervals_event_id: str
    content_hash: str


@dataclass
class PlannedSession:
    session_date: date
    week_number: int
    day_of_week: int
    spec: SessionSpec


def iter_horizon_sessions(
    plan: TrainingPlanSchema,
    horizon_start: date,
    horizon_end: date,
    *,
    today: date | None = None,
) -> list[PlannedSession]:
    """Every session whose planned date falls in [horizon_start, horizon_end], ordered
    by date. A week with no `start_date` is skipped rather than guessed.

    Past-dated sessions (`session_date < today`) are excluded from every path that reads
    this — publish, republish diff, withdrawal, and the approval content hash — so a
    plan change never writes or rewrites a session in the past (FR-011, FR-022). `today`
    defaults to `date.today()`; pass it explicitly only in tests.
    """
    cutoff = today or date.today()
    out: list[PlannedSession] = []
    for week in plan.weeks:
        if week.start_date is None:
            continue
        for spec in week.sessions:
            session_date = week.start_date + timedelta(days=spec.day_of_week)
            if session_date < cutoff:
                continue
            if horizon_start <= session_date <= horizon_end:
                out.append(
                    PlannedSession(session_date, week.week_number, spec.day_of_week, spec)
                )
    out.sort(key=lambda p: p.session_date)
    return out


def remote_event_hash(remote_event: dict) -> str | None:
    """The content fingerprint of a calendar event as it currently stands remotely,
    computed from the same fields we write (date | name | description) so it is directly
    comparable to a PublishedEntry.content_hash.

    Content-based, deliberately: it sidesteps research open question 3 (whether the
    event's `updated` timestamp changes on our own writes as well as athlete edits).
    Assumes intervals.icu echoes `description` back unmodified — the one thing the T046
    probe still needs to confirm on the live account before US5 is trusted in anger.
    """
    raw = str(remote_event.get("start_date_local") or "")[:10]
    try:
        session_date = date.fromisoformat(raw)
    except ValueError:
        return None
    name = str(remote_event.get("name") or "")
    description = str(remote_event.get("description") or "")
    return hash_session_content(session_date, name, description)


async def withdraw_event(client: IntervalsClient, intervals_event_id: str) -> None:
    """Remove one calendar event we published (FR-019, FR-021). Only ever called with a
    PublishedEntry's own event id — the prefix scoping that protects foreign entries
    lives one layer up, in the service that decides *which* entries to withdraw."""
    await client.delete_event(intervals_event_id)


async def publish_sessions(
    client: IntervalsClient,
    plan: TrainingPlanSchema,
    plan_id: uuid.UUID,
    horizon_start: date,
    horizon_end: date,
    *,
    known_entries: dict[str, KnownEntry] | None = None,
    today: date | None = None,
) -> list[SessionOutcome]:
    """Converge the athlete's calendar to the in-horizon plan, idempotently (FR-014,
    FR-017, SC-003) — the API has no upsert, so re-POSTing would duplicate (research R2).

    The diff (research R2's prescribed shape):
      1. `list_events()` the window and keep only `external_id`-prefixed entries — FR-015
         makes "never touch what we didn't create" true by construction: anything without
         our prefix is invisible here.
      2. For each planned session: if a `known_entries` row already records this exact
         `content_hash` *and* the event is present remotely -> skip ("unchanged"). This is
         also what makes an interrupted run resumable (FR-016): the sessions written
         before the interruption are recognised and not rewritten.
      3. Else if the event exists remotely -> `update_event()` ("updated").
      4. Else -> `create_event()` ("created").

    A session without steps is a refusal (FR-009); a per-session API failure is "failed"
    and does not abandon the rest (FR-028). A mid-batch connection failure therefore
    leaves the calendar coherent — every session already written is in `outcomes` as
    "created"/"updated" and gets a PublishedEntry row from the caller, so a retry
    resumes rather than duplicating (FR-026, FR-016).

    Quota (FR-027, SC-010): a full horizon is 1 `list_events` + one write per session ≈
    up to ~13 requests (current + following week, ~6 sessions each). Documented quotas
    are ~5000/day and ~2500/15min (spec 002 research R4) — three orders of magnitude of
    headroom. Confirmed by arithmetic, deliberately not stress-tested: exhausting the
    athlete's real quota to observe the failure is not worth doing (same call spec 002
    made about its own rate-limit question).
    """
    known_entries = known_entries or {}
    # A list_events failure propagates — the caller surfaces staleness to the athlete
    # rather than letting a half-known window look current (FR-026).
    remote = await client.list_events(
        oldest=horizon_start.isoformat(), newest=horizon_end.isoformat()
    )
    remote_by_ext: dict[str, dict] = {
        e["external_id"]: e
        for e in remote
        if str(e.get("external_id") or "").startswith(EXTERNAL_ID_PREFIX)
    }

    outcomes: list[SessionOutcome] = []
    for planned in iter_horizon_sessions(plan, horizon_start, horizon_end, today=today):
        spec = planned.spec
        external_id = build_external_id(
            plan_id,
            planned.session_date,
            spec.workout_type,
            planned.week_number,
            planned.day_of_week,
        )
        name = spec.description_fr or spec.workout_type

        def _outcome(status: str, *, _p=planned, _n=name, _x=external_id, **kw) -> SessionOutcome:
            return SessionOutcome(
                session_date=_p.session_date,
                week_number=_p.week_number,
                day_of_week=_p.day_of_week,
                workout_type=_p.spec.workout_type,
                name=_n,
                external_id=_x,
                status=status,
                **kw,
            )

        if spec.steps is None:
            outcomes.append(
                _outcome("refused", detail="séance sans structure (plan pré-004) — non publiable")
            )
            continue

        try:
            rendered = render_dsl(spec.steps, plan.zones)
        except EmptySessionError as exc:
            outcomes.append(_outcome("refused", detail=str(exc)))
            continue

        content_hash = hash_session_content(planned.session_date, name, rendered)
        remote_event = remote_by_ext.get(external_id)
        known = known_entries.get(external_id)

        # US5 — never overwrite or recreate what the athlete touched (FR-023/FR-024),
        # never modify a completed activity (FR-025).
        if remote_event is not None and remote_event.get("category") not in (None, "WORKOUT"):
            outcomes.append(
                _outcome(
                    "refused",
                    intervals_event_id=str(remote_event.get("id")),
                    detail="l'entrée est devenue une activité réalisée — jamais modifiée (FR-025)",
                )
            )
            continue
        if known is not None and remote_event is None:
            outcomes.append(
                _outcome(
                    "conflict",
                    content_hash=content_hash,
                    rendered_dsl=rendered,
                    detail="supprimée manuellement dans intervals.icu — non recréée (FR-024)",
                )
            )
            continue
        if known is not None and remote_event is not None:
            remote_hash = remote_event_hash(remote_event)
            if (
                remote_hash is not None
                and remote_hash != known.content_hash
                and remote_hash != content_hash
            ):
                outcomes.append(
                    _outcome(
                        "conflict",
                        intervals_event_id=str(remote_event.get("id")),
                        content_hash=content_hash,
                        rendered_dsl=rendered,
                        detail="modifiée à la main dans intervals.icu — non écrasée (FR-023)",
                    )
                )
                continue

        if known is not None and known.content_hash == content_hash and remote_event is not None:
            outcomes.append(
                _outcome(
                    "unchanged",
                    intervals_event_id=known.intervals_event_id,
                    content_hash=content_hash,
                    rendered_dsl=rendered,
                )
            )
            continue

        payload = build_event_payload(planned.session_date, name, external_id, rendered)
        try:
            if remote_event is not None:
                event = await client.update_event(str(remote_event.get("id")), payload)
                status = "updated"
            else:
                event = await client.create_event(payload)
                status = "created"
        except Exception as exc:  # noqa: BLE001 — one bad session must not sink the batch (FR-028)
            outcomes.append(
                _outcome(
                    "failed",
                    content_hash=content_hash,
                    rendered_dsl=rendered,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        event_id = str(event.get("id") if event else remote_event.get("id"))

        # A structure intervals.icu cannot represent is reported on the event's
        # `push_errors` field rather than as an HTTP error (research open question 2) —
        # the write "succeeded" but the workout will not reach the device. Surface it
        # specifically as a refusal, and (for a fresh create) delete the useless event
        # so a retry is clean. The rest of the batch is unaffected (FR-028).
        push_errors = (event or {}).get("push_errors")
        if push_errors:
            if status == "created":
                try:
                    await client.delete_event(event_id)
                except Exception:  # noqa: BLE001
                    pass
            outcomes.append(
                _outcome(
                    "refused",
                    content_hash=content_hash,
                    rendered_dsl=rendered,
                    detail=f"structure refusée par le calendrier : {push_errors}",
                )
            )
            continue

        outcomes.append(
            _outcome(
                status,
                intervals_event_id=event_id,
                content_hash=content_hash,
                rendered_dsl=rendered,
            )
        )
    return outcomes
