"""Calendar-publication orchestration (spec 005): approval lifecycle, plan<->calendar
diffing, divergence detection.

Provider-agnostic by design (plan.md Constitution Check III): the workout DSL and the
events API live in app/providers/intervals/; this module owns consent and record-keeping
and never imports aiogram. app/engine/ is not touched at all — this feature reads
sessions, it does not generate or modify them.

request_publication (T016) records consent; authorize_publication (T022) is the gate
every write path passes before anything reaches calendar.py — it refuses a missing,
wrong-plan, un-approved, or content-stale approval (FR-001, FR-004). execute_publication
(T018) calls it as its first statement.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t, tp
from app.db.models.publication import PublicationApproval
from app.db.models.training_plan import TrainingPlan
from app.db.models.user import User
from app.db.repositories import publication_repo
from app.engine.schemas import TrainingPlanSchema
from app.providers.intervals.calendar import (
    KnownEntry,
    iter_horizon_sessions,
    publish_sessions,
    remote_event_hash,
    withdraw_event,
)
from app.providers.intervals.client import IntervalsClient
from app.providers.intervals.workout_dsl import (
    EmptySessionError,
    hash_session_content,
    render_dsl,
)

_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _short_date(value: date) -> str:
    return t(
        "publication.short_date",
        day=f"{value.day:02d}",
        month=f"{value.month:02d}",
    )


def _weekday(value: date) -> str:
    return t(f"publication.weekday_{_WEEKDAY_KEYS[value.weekday()]}")


# The canonical per-session content hash lives in the pure format module so provider
# I/O and this orchestrator hash identically (data-model.md §Content hashing). Kept
# re-exported here under its original name for callers within the service layer.
hash_content = hash_session_content


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
        t("publication.preview_title"),
        "",
        t(
            "publication.preview_period",
            start_weekday=_weekday(horizon_start),
            start_date=_short_date(horizon_start),
            end_weekday=_weekday(horizon_end),
            end_date=_short_date(horizon_end),
            weeks=weeks_span,
            week_unit=t(
                "publication.weeks_plural" if weeks_span > 1 else "publication.week_singular"
            ),
        ),
        "",
    ]
    for planned in sessions:
        spec = planned.spec
        name = spec.description_fr or spec.workout_type
        flag = "" if spec.steps is not None else t("publication.unstructured_flag")
        lines.append(
            t(
                "publication.preview_session",
                weekday=_weekday(planned.session_date),
                date=_short_date(planned.session_date),
                name=name,
                minutes=spec.duration_minutes,
                flag=flag,
            )
        )
    publishable = sum(1 for p in sessions if p.spec.steps is not None)
    lines += ["", tp("publication.preview_total", publishable)]
    if is_first_publication:
        lines += ["", t("publication.device_caveat")]
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


class PublicationNotAuthorized(Exception):
    """No usable approval stands behind a write attempt — no such approval, wrong
    athlete, wrong plan, or not in `approved` state (FR-001)."""


class StaleApprovalError(PublicationNotAuthorized):
    """The plan's current content no longer matches what the athlete approved (FR-004).
    A fresh approval is required — publication must not silently re-authorise itself."""


async def authorize_publication(
    session: AsyncSession, approval_id, plan: TrainingPlan
) -> PublicationApproval:
    """The single gate every write path must pass before anything reaches calendar.py
    (FR-001, FR-004). Recomputes the plan's current content hash over the approved
    horizon and refuses if it differs from what was shown.

    Called by execute_publication() and by every future write path (withdrawal in US4,
    republish diffing in US3) — enumerated by grep at T026, not by trust.
    """
    approval = await publication_repo.get_approval(session, approval_id)
    if approval is None:
        raise PublicationNotAuthorized(f"no PublicationApproval {approval_id}")
    if approval.user_id != plan.user_id:
        raise PublicationNotAuthorized("approval belongs to a different athlete")
    if approval.plan_id != plan.id:
        raise PublicationNotAuthorized("approval was recorded for a different plan")
    if approval.status != "approved":
        raise PublicationNotAuthorized(
            f"approval {approval_id} is {approval.status!r}, not 'approved'"
        )

    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    current_hash = plan_content_hash(
        schema, approval.horizon_start, approval.horizon_end
    )
    if current_hash != approval.content_hash:
        raise StaleApprovalError(
            "the plan changed since this approval was given — fresh approval required "
            "(FR-004)"
        )
    return approval


# ── US4: the calendar tracks the plan, and staleness is never silent ─────────


@dataclass
class Divergence:
    session_date: date
    name: str
    kind: str  # "changed" | "removed"


def _current_session_hash(
    schema: TrainingPlanSchema, week_number: int, day_of_week: int
) -> tuple[date | None, str, str | None]:
    """(current planned date, display name, content hash) for the session now sitting at
    (week, day-of-week), or (None, "", None) if the plan no longer has one there."""
    week = next((w for w in schema.weeks if w.week_number == week_number), None)
    if week is None or week.start_date is None:
        return None, "", None
    spec = next((s for s in week.sessions if s.day_of_week == day_of_week), None)
    if spec is None:
        return None, "", None
    session_date = week.start_date + timedelta(days=day_of_week)
    name = spec.description_fr or spec.workout_type
    if spec.steps is None:
        return session_date, name, None
    try:
        rendered = render_dsl(spec.steps, schema.zones)
    except EmptySessionError:
        return session_date, name, None
    return session_date, name, hash_content(session_date, name, rendered)


def check_divergence(schema: TrainingPlanSchema, entries) -> list[Divergence]:
    """"Plan moved ahead of calendar" (data-model.md §Divergence, FR-020): a live
    PublishedEntry whose slot in the current plan either changed content or vanished.
    Derived on demand — a stored flag would go stale exactly when it matters."""
    out: list[Divergence] = []
    for e in entries:
        if e.withdrawn_at is not None:
            continue
        cur_date, name, cur_hash = _current_session_hash(
            schema, e.week_number, e.day_of_week
        )
        if cur_date is None:
            out.append(Divergence(e.session_date, t("publication.removed_session_name"), "removed"))
        elif cur_hash != e.content_hash:
            out.append(Divergence(cur_date, name, "changed"))
    return out


def detect_athlete_edit(remote_event: dict, entry) -> bool:
    """The remote event's current content differs from what we last wrote for this
    entry (FR-023). Content-based (date | name | description) rather than relying on the
    event's `updated` timestamp — see calendar.remote_event_hash and research open
    question 3. A `None` (unparseable) remote hash is treated as "not an edit" so a
    malformed event never triggers a false conflict."""
    rh = remote_event_hash(remote_event)
    return rh is not None and rh != entry.content_hash


def describe_divergence_for_coach(schema: TrainingPlanSchema, entries) -> str | None:
    """A short note for the LLM system prompt so the coach tells the athlete the
    calendar is out of date rather than talking as if it were current (FR-020, T038).
    None when calendar and plan agree."""
    divs = check_divergence(schema, entries)
    if not divs:
        return None
    lines = [t("publication.divergence_heading")]
    for d in divs:
        verb = t(
            "publication.divergence_changed" if d.kind == "changed"
            else "publication.divergence_removed"
        )
        lines.append(
            t(
                "publication.divergence_item",
                date=_short_date(d.session_date),
                name=d.name,
                status=verb,
            )
        )
    lines.append(t("publication.divergence_instruction"))
    return "\n".join(lines)


@dataclass
class PublicationReport:
    text: str
    created: int
    updated: int
    unchanged: int
    withdrawn: int
    conflict: int
    refused: int
    failed: int


async def execute_publication(
    session: AsyncSession,
    client: IntervalsClient,
    user: User,
    plan: TrainingPlan,
    approval: PublicationApproval,
) -> PublicationReport:
    """Run the approved publication: converge the calendar to the plan (idempotent —
    US3), persist/update a PublishedEntry per written session carrying the authorising
    approval_id (FR-005), and build the per-session report — never a blanket "done"
    (FR-006).

    Gated: authorize_publication() runs before a single event is written, so a stale or
    missing approval refuses here rather than deep in the batch (FR-001, FR-004).
    Resumable: existing PublishedEntry rows tell publish_sessions what was already
    written, so a retried interrupted run converges to the same state (FR-016)."""
    approval = await authorize_publication(session, approval.id, plan)
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)

    active = await publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    entries_by_ext = {e.external_id: e for e in active}
    known = {
        ext: KnownEntry(intervals_event_id=e.intervals_event_id, content_hash=e.content_hash)
        for ext, e in entries_by_ext.items()
    }

    outcomes = await publish_sessions(
        client,
        schema,
        plan.id,
        approval.horizon_start,
        approval.horizon_end,
        known_entries=known,
    )

    counts = {
        "created": 0, "updated": 0, "unchanged": 0, "withdrawn": 0,
        "conflict": 0, "refused": 0, "failed": 0,
    }
    lines: list[str] = []
    for o in outcomes:
        counts[o.status] += 1
        label = f"{_weekday(o.session_date)} {_short_date(o.session_date)}  {o.name}"
        if o.status in ("created", "updated", "unchanged"):
            mark = {"created": "✅", "updated": "✅", "unchanged": "✓"}[o.status]
            suffix = {
                "created": "",
                "updated": t("publication.status_updated_suffix"),
                "unchanged": t("publication.status_unchanged_suffix"),
            }[o.status]
            lines.append(f"  {mark} {label}{suffix}")
            existing = entries_by_ext.get(o.external_id)
            if existing is None:
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
                    content_hash=o.content_hash or "",
                )
            elif o.status != "unchanged":
                await publication_repo.update_published_entry(
                    session,
                    existing.id,
                    intervals_event_id=o.intervals_event_id,
                    content_hash=o.content_hash,
                    approval_id=approval.id,
                )
        elif o.status == "conflict":
            # Athlete edited or deleted our entry — surfaced, never overwritten or
            # recreated (FR-023, FR-024). The DB row is left exactly as it was.
            lines.append(f"  ✋ {label} — {o.detail}")
        else:  # refused | failed
            lines.append(f"  ❌ {label} — {o.detail}")

    # Withdraw entries whose session is no longer in the plan for this horizon (FR-019).
    # Never a past-dated one (FR-022), never a foreign entry (these are our own rows).
    today = date.today()
    planned_ext = {o.external_id for o in outcomes}
    for entry in active:
        if entry.withdrawn_at is not None or entry.external_id in planned_ext:
            continue
        if entry.session_date < today:
            continue
        if not (approval.horizon_start <= entry.session_date <= approval.horizon_end):
            continue
        wlabel = f"{_weekday(entry.session_date)} {_short_date(entry.session_date)}"
        try:
            await withdraw_event(client, entry.intervals_event_id)
        except Exception as exc:  # noqa: BLE001
            lines.append(t("publication.withdraw_failed_line", label=wlabel, error=exc))
            continue
        await publication_repo.mark_withdrawn(session, entry.id)
        counts["withdrawn"] += 1
        lines.append(t("publication.withdrawn_line", label=wlabel))

    written = counts["created"] + counts["updated"]
    header_bits = [tp("publication.header_published", written)]
    if counts["unchanged"]:
        header_bits.append(t("publication.header_unchanged", count=counts["unchanged"]))
    if counts["withdrawn"]:
        n = counts["withdrawn"]
        header_bits.append(tp("publication.header_withdrawn", n))
    if counts["conflict"]:
        n = counts["conflict"]
        header_bits.append(tp("publication.header_conflict", n))
    if counts["refused"]:
        n = counts["refused"]
        header_bits.append(tp("publication.header_refused", n))
    if counts["failed"]:
        n = counts["failed"]
        header_bits.append(tp("publication.header_failed", n))
    ok = not (counts["refused"] or counts["failed"] or counts["conflict"])
    header = ("✅ " if ok else "⚠️ ") + ", ".join(header_bits)

    body = [header, "", *lines]
    if counts["conflict"]:
        body += ["", t("publication.conflict_note")]

    return PublicationReport(
        text="\n".join(body),
        created=counts["created"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        withdrawn=counts["withdrawn"],
        conflict=counts["conflict"],
        refused=counts["refused"],
        failed=counts["failed"],
    )


async def withdraw_all_publications(
    session: AsyncSession,
    client: IntervalsClient,
    user: User,
    plan: TrainingPlan,
) -> tuple[int, int]:
    """Remove every live entry this system published for the plan (FR-021, SC-006).

    Iterates our own PublishedEntry rows only — a foreign `cycling-coach:` entry is
    never in that set, so "100% of ours, 0% of anything else" is true by construction,
    not by filtering. Returns (withdrawn, failed).

    No approval gate: withdrawing content the athlete asked to remove *is* the explicit
    request (the /unpublish confirmation step), unlike a write which needs prior consent.
    """
    active = await publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    withdrawn = failed = 0
    for entry in active:
        try:
            await withdraw_event(client, entry.intervals_event_id)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        await publication_repo.mark_withdrawn(session, entry.id)
        withdrawn += 1
    return withdrawn, failed


@dataclass
class FreestylePublishOutcome:
    """spec 010 — deliberately not calendar.SessionOutcome: that dataclass requires
    week_number/day_of_week, which don't exist for a session outside any plan (research.md
    Decision 4's reasoning applied to the outcome shape too, not just the DB table)."""

    status: str  # "created" | "refused" | "failed"
    external_id: str | None = None
    intervals_event_id: str | None = None
    content_hash: str | None = None
    detail: str | None = None


async def publish_freestyle_session(
    client: IntervalsClient,
    session_date: date,
    name: str,
    workout_type: str,
    steps: list,
    zones: dict,
) -> FreestylePublishOutcome:
    """Write one freestyle session to the calendar — always a create, never a diff
    against a remote event (research.md Decision 6: a freestyle publish has nothing to
    reconcile against, unlike publish_sessions()'s plan-horizon convergence)."""
    from app.providers.intervals.calendar import (
        build_event_payload,
        build_freestyle_external_id,
    )

    try:
        rendered = render_dsl(steps, zones)
    except EmptySessionError as exc:
        return FreestylePublishOutcome(status="refused", detail=str(exc))

    content_hash = hash_session_content(session_date, name, rendered)
    external_id = build_freestyle_external_id(session_date, workout_type)
    payload = build_event_payload(session_date, name, external_id, rendered)

    try:
        event = await client.create_event(payload)
    except Exception as exc:  # noqa: BLE001 — mirrors publish_sessions()'s per-item handling
        return FreestylePublishOutcome(
            status="failed", detail=f"{type(exc).__name__}: {exc}"
        )

    event_id = str(event.get("id"))

    # Same push_errors handling publish_sessions() already has (research.md Decision 6:
    # reuse, don't reinvent) — the write "succeeded" but the structure can't reach a
    # device, so clean up the useless event and report a refusal.
    push_errors = (event or {}).get("push_errors")
    if push_errors:
        try:
            await client.delete_event(event_id)
        except Exception:  # noqa: BLE001
            pass
        return FreestylePublishOutcome(
            status="refused",
            detail=t("publication.freestyle_structure_refused", errors=push_errors),
        )

    return FreestylePublishOutcome(
        status="created",
        external_id=external_id,
        intervals_event_id=event_id,
        content_hash=content_hash,
    )


async def withdraw_freestyle_publications(
    session: AsyncSession, client: IntervalsClient, user: User,
) -> tuple[int, int]:
    """Every still-active freestyle publication for this user (US3, FR-011) — mirrors
    withdraw_all_publications() but over freestyle_publication_repo's own table, never
    touching a plan-published entry (a different table, not a filter that could be
    gotten wrong — research.md Decision 4)."""
    from app.db.repositories import freestyle_publication_repo

    active = await freestyle_publication_repo.get_active_for_user(session, user.id)
    withdrawn = failed = 0
    for entry in active:
        try:
            await withdraw_event(client, entry.intervals_event_id)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        await freestyle_publication_repo.mark_withdrawn(session, entry.id)
        withdrawn += 1
    return withdrawn, failed
