"""Calendar-publication orchestration (spec 005): approval lifecycle, plan<->calendar
diffing, divergence detection.

Provider-agnostic by design (plan.md Constitution Check III): the workout DSL and the
events API live in app/providers/intervals/; this module owns consent and record-keeping
and never imports aiogram. app/engine/ is not touched at all — this feature reads
sessions, it does not generate or modify them.

Phase 3 (US1): request_publication (T016) + execute_publication (T018). The content-hash
gate that refuses a stale approval (FR-004) lands with US2 (T022).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.publication import PublicationApproval
from app.db.models.training_plan import TrainingPlan
from app.db.models.user import User
from app.db.repositories import publication_repo
from app.engine.schemas import TrainingPlanSchema
from app.providers.intervals.calendar import (
    iter_horizon_sessions,
    publish_sessions,
)
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.workout_dsl import EmptySessionError, render_dsl

_WEEKDAY_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]

_DEVICE_CAVEAT = (
    "⚠️ Pour que ces séances arrivent sur ta montre, le transfert vers ton appareil "
    "doit être activé dans TES réglages intervals.icu — je ne peux pas le faire à ta "
    "place. (Réglages → « Sync your calendar to your device » / ton app Garmin/Wahoo)"
)


def hash_content(session_date: date, name: str, rendered_dsl: str) -> str:
    """SHA-256( session_date | name | rendered_DSL_text ) — data-model.md §Content hashing.

    The rendered DSL is what actually reaches the calendar and what the athlete is shown
    in the approval request, so hashing it binds the approval to precisely the content it
    was shown for (FR-004) and lets a published entry be compared against the current
    plan to detect drift (FR-020) with no ambiguity about which fields count.

    Deliberately excluded: intervals_event_id (server-assigned, not content), approval_id
    (provenance), load/duration (derived by intervals.icu from the DSL itself — R4 — so
    including them would double-count the same information).
    """
    payload = f"{session_date.isoformat()}|{name}|{rendered_dsl}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _current_week_number(schema: TrainingPlanSchema, plan_start: date) -> int:
    today = date.today()
    if today < plan_start:
        return 1
    week_num = (today - plan_start).days // 7 + 1
    return min(max(week_num, 1), schema.weeks_count)


def compute_horizon(schema: TrainingPlanSchema, plan_start: date) -> tuple[date, date]:
    """Current week + the following one (FR-010) — a bounded period, stated back to the
    athlete rather than left as an invisible constant. Publishing a whole multi-month
    plan would fill their calendar with sessions certain to change."""
    current = _current_week_number(schema, plan_start)
    weeks = [w for w in schema.weeks if w.week_number in (current, current + 1)]
    dated = [w.start_date for w in weeks if w.start_date is not None]
    if not dated:
        # No per-week dates (shouldn't happen post spec-004) — derive from plan_start.
        start = plan_start + timedelta(days=(current - 1) * 7)
        return start, start + timedelta(days=13)
    return min(dated), max(dated) + timedelta(days=6)


def plan_content_hash(
    schema: TrainingPlanSchema, horizon_start: date, horizon_end: date
) -> str:
    """One hash over every in-horizon session, in date order — the fingerprint an
    approval is bound to (FR-004) and the current-plan side of divergence detection
    (FR-020). A session with no steps contributes a stable marker so removing or adding
    one still changes the fingerprint."""
    parts: list[str] = []
    for planned in iter_horizon_sessions(schema, horizon_start, horizon_end):
        spec = planned.spec
        name = spec.description_fr or spec.workout_type
        if spec.steps is None:
            parts.append(f"{planned.session_date.isoformat()}|{name}|<no-steps>")
            continue
        try:
            rendered = render_dsl(spec.steps, schema.zones)
        except EmptySessionError:
            parts.append(f"{planned.session_date.isoformat()}|{name}|<no-steps>")
            continue
        parts.append(hash_content(planned.session_date, name, rendered))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass
class PublicationRequest:
    approval: PublicationApproval
    text: str
    session_count: int


def build_approval_request_text(
    schema: TrainingPlanSchema,
    horizon_start: date,
    horizon_end: date,
    *,
    is_first_publication: bool,
) -> tuple[str, int]:
    """The approval contract with the athlete (contracts/calendar-publication.md §3):
    every session that will be written, with its date — not a count, not a summary
    (FR-002) — the horizon stated (FR-010), and the device-forwarding caveat before the
    first publication (FR-012, SC-009)."""
    sessions = iter_horizon_sessions(schema, horizon_start, horizon_end)
    weeks_span = (horizon_end - horizon_start).days // 7 + 1

    lines = [
        "📤 <b>Publier vers ton calendrier intervals.icu</b>",
        "",
        f"Période : {_WEEKDAY_FR[horizon_start.weekday()]} {horizon_start.strftime('%d/%m')} "
        f"→ {_WEEKDAY_FR[horizon_end.weekday()]} {horizon_end.strftime('%d/%m')} "
        f"({weeks_span} semaine{'s' if weeks_span > 1 else ''})",
        "",
    ]
    for planned in sessions:
        spec = planned.spec
        name = spec.description_fr or spec.workout_type
        flag = "" if spec.steps is not None else "  ⚠️ sans structure, ne sera pas publiée"
        lines.append(
            f"  {_WEEKDAY_FR[planned.session_date.weekday()]} "
            f"{planned.session_date.strftime('%d/%m')}  {name}  "
            f"— {spec.duration_minutes} min{flag}"
        )
    publishable = sum(1 for p in sessions if p.spec.steps is not None)
    lines += ["", f"{publishable} séance{'s' if publishable > 1 else ''} au total."]
    if is_first_publication:
        lines += ["", _DEVICE_CAVEAT]
    return "\n".join(lines), publishable


async def request_publication(
    session: AsyncSession, user: User, plan: TrainingPlan
) -> PublicationRequest:
    """Compute the horizon (FR-010), build the athlete-facing request, and record a
    `pending` PublicationApproval bound to the plan's current content hash (FR-004)."""
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    horizon_start, horizon_end = compute_horizon(schema, plan.start_date)

    prior = await publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    is_first = not prior

    text, publishable = build_approval_request_text(
        schema, horizon_start, horizon_end, is_first_publication=is_first
    )
    content_hash = plan_content_hash(schema, horizon_start, horizon_end)

    approval = await publication_repo.create_approval(
        session,
        user_id=user.id,
        plan_id=plan.id,
        content_hash=content_hash,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        session_count=publishable,
    )
    return PublicationRequest(approval=approval, text=text, session_count=publishable)


@dataclass
class PublicationReport:
    text: str
    created: int
    refused: int
    failed: int


async def execute_publication(
    session: AsyncSession,
    client: IntervalsClient,
    user: User,
    plan: TrainingPlan,
    approval: PublicationApproval,
) -> PublicationReport:
    """Run the approved publication: write the events, persist a PublishedEntry per
    written session carrying the authorising approval_id (FR-005), and build the
    per-session report — never a blanket "done" (FR-006)."""
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    outcomes = await publish_sessions(
        client, schema, plan.id, approval.horizon_start, approval.horizon_end
    )

    created = refused = failed = 0
    lines: list[str] = []
    for o in outcomes:
        label = (
            f"{_WEEKDAY_FR[o.session_date.weekday()]} "
            f"{o.session_date.strftime('%d/%m')}  {o.name}"
        )
        if o.status == "created":
            created += 1
            lines.append(f"  ✅ {label}")
            await publication_repo.create_published_entry(
                session,
                user_id=user.id,
                plan_id=plan.id,
                approval_id=approval.id,
                external_id=o.external_id,
                intervals_event_id=o.intervals_event_id or "",
                session_date=o.session_date,
                week_number=o.week_number,
                day_of_week=o.day_of_week,
                content_hash=hash_content(o.session_date, o.name, o.rendered_dsl or ""),
            )
        elif o.status == "refused":
            refused += 1
            lines.append(f"  ❌ {label} — {o.detail}")
        else:
            failed += 1
            lines.append(f"  ❌ {label} — {o.detail}")

    header_bits = [f"{created} publiée{'s' if created != 1 else ''}"]
    if refused:
        header_bits.append(f"{refused} refusée{'s' if refused != 1 else ''}")
    if failed:
        header_bits.append(f"{failed} échec{'s' if failed != 1 else ''}")
    header = ("✅ " if not (refused or failed) else "⚠️ ") + ", ".join(header_bits)

    return PublicationReport(
        text="\n".join([header, "", *lines]),
        created=created,
        refused=refused,
        failed=failed,
    )
