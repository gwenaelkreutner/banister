"""Session design for an external agent: browse, evaluate, propose, publish on approval.

The division of labour behind `app/api/agent.py`: the agent chooses or composes a session,
this module and the engine compute everything about it (Constitution Principle I), and only
the athlete's approval writes to the calendar. Findings are reported, never blocking: the
athlete decides. Only a session that cannot exist (bad structure, unknown zone) is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, timedelta

from pydantic import BaseModel, Field, TypeAdapter, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.core.time import paris_today
from app.db import repositories as repo
from app.db.models.publication import SessionProposal
from app.db.models.user import User
from app.engine.fitting import FittingError, fit_template
from app.engine.freestyle_selector import (
    HARD_EFFORT_COOLDOWN_DAYS,
    VALID_WORKOUT_TYPES,
    choose_workout_type,
)
from app.engine.schemas import (
    AthleteProfileSchema,
    RepeatGroup,
    Step,
    derive_duration_minutes,
    derive_zone_code,
)
from app.engine.session_library import SessionTemplate, load_library
from app.engine.session_render import render_description
from app.engine.training_history import daily_training_tss
from app.engine.tss import estimate_structured_session_tss
from app.engine.zones import compute_hr_zones, compute_power_zones
from app.providers.intervals.client import IntervalsClient
from app.services.freestyle_suggestion import gather_freestyle_inputs
from app.services.guardrail_service import assemble_recovery_findings, assemble_workload_findings
from app.services.publication import publish_and_record_freestyle
from app.services.sport_context import _finding

MAX_SESSION_MINUTES = 600
MAX_DAYS_AHEAD = 28
HISTORY_DAYS = 89
RECENT_PEAK_DAYS = 42
# Same margin the freestyle selector already warns at (spec 011).
REFERENCE_LOAD_MARGIN = 1.15
_ZONE_CODE = re.compile(r"^Z[1-6]$")
_STEPS = TypeAdapter(list[Step | RepeatGroup])


class SessionDesignError(ValueError):
    """The request cannot be honoured. `status` maps to the HTTP answer."""

    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


class SessionDraft(BaseModel):
    """A session to evaluate: a library template (optionally fitted to a duration), or a
    structure composed by the agent. Zones only, never watts: see Step."""

    date: date
    template_id: str | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=MAX_SESSION_MINUTES)
    steps: list[Step | RepeatGroup] | None = None
    workout_type: str | None = None
    title: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _one_source(self) -> SessionDraft:
        if bool(self.template_id) == bool(self.steps):
            raise ValueError("give exactly one of template_id or steps")
        if self.steps and self.duration_minutes is not None:
            raise ValueError("duration_minutes only applies to template_id")
        if self.steps and self.workout_type not in VALID_WORKOUT_TYPES:
            raise ValueError(f"steps need workout_type, one of {sorted(VALID_WORKOUT_TYPES)}")
        return self


def _template_entry(template: SessionTemplate, *, detail: bool) -> dict:
    steps = list(template.structure)
    entry = {
        "id": template.id,
        "workout_type": template.workout_type,
        "family": template.family,
        "phases": list(template.phases),
        "duration_minutes": derive_duration_minutes(steps),
        "scalable": template.scaling is not None,
        "purpose": template.purpose,
    }
    if detail:
        entry["intent"] = " ".join(template.intent.split())
        entry["suits"] = " ".join(template.suits.split())
        entry["structure"] = [s.model_dump(mode="json") for s in steps]
    return entry


def session_catalog(
    *,
    workout_type: str | None = None,
    family: str | None = None,
    phase: str | None = None,
    query: str | None = None,
    max_minutes: int | None = None,
    detail: bool = False,
) -> list[dict]:
    """The session library, filtered. `query` matches every word against the description."""
    words = (query or "").lower().split()
    entries = []
    for template in load_library():
        if workout_type and template.workout_type != workout_type:
            continue
        if family and template.family != family:
            continue
        if phase and phase not in template.phases:
            continue
        text = (
            f"{template.id} {template.family} {template.purpose} {template.intent} {template.suits}"
        )
        if not all(word in text.lower() for word in words):
            continue
        entry = _template_entry(template, detail=detail)
        # A scalable template may still fit: the limit only excludes fixed ones.
        if max_minutes and not template.scaling and entry["duration_minutes"] > max_minutes:
            continue
        entries.append(entry)
    return entries


async def load_profile(session: AsyncSession, user: User) -> AthleteProfileSchema:
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    if row is None or not row.profile:
        raise SessionDesignError("the athlete has no profile yet", status=409)
    return AthleteProfileSchema.model_validate(row.profile)


async def load_recent_training(session: AsyncSession, user: User, today: date) -> tuple[list, list]:
    start = today - timedelta(days=HISTORY_DAYS)
    logs = await repo.session_log_repo.get_in_range(session, user.id, start, today)
    activities = await repo.activity_repo.get_in_range(session, user.id, start, today)
    return logs, activities


def _zones_for(profile: AthleteProfileSchema) -> dict | None:
    """`None` when the athlete's reference is unknown: never a default threshold
    (Constitution Principle IV). Such a session can be discussed but not published."""
    if profile.coaching_mode == "power":
        ftp = profile.equipment.ftp
        return compute_power_zones(ftp) if ftp else None
    physio = profile.physio
    if not (physio and physio.hr_max and physio.hr_rest):
        return None
    return compute_hr_zones(physio.hr_max, physio.hr_rest)


def _resolve_structure(
    draft: SessionDraft, profile: AthleteProfileSchema
) -> tuple[str, list[Step | RepeatGroup], str | None]:
    if draft.steps:
        return draft.workout_type, list(draft.steps), None
    template = next((x for x in load_library() if x.id == draft.template_id), None)
    if template is None:
        raise SessionDesignError(f"unknown template_id {draft.template_id!r}")
    if draft.duration_minutes is None:
        return template.workout_type, list(template.structure), template.id
    try:
        fitted = fit_template(
            template,
            0.0,  # unused: a requested duration takes precedence over a load target
            coaching_mode=profile.coaching_mode,
            ftp=profile.equipment.ftp,
            requested_duration_minutes=draft.duration_minutes,
        )
    except FittingError as exc:
        raise SessionDesignError(str(exc)) from exc
    return template.workout_type, list(fitted.steps), template.id


def _flat_steps(steps: list[Step | RepeatGroup]) -> list[Step]:
    flat: list[Step] = []
    for item in steps:
        flat.extend(item.steps if isinstance(item, RepeatGroup) else [item])
    return flat


def _targets(steps: list[Step | RepeatGroup], zones: dict | None) -> dict:
    if zones is None:
        return {}
    out = {}
    for code in sorted({s.zone_code for s in _flat_steps(steps)}):
        zone = zones[code]
        out[code] = {
            "name": zone.name,
            "watts": [zone.lower_watts, zone.upper_watts] if zone.lower_watts is not None else None,
            "bpm": [zone.lower_bpm, zone.upper_bpm] if zone.lower_bpm is not None else None,
        }
    return out


def _recent_peak_day(items: list, today: date) -> dict | None:
    daily = daily_training_tss(items, start=today - timedelta(days=RECENT_PEAK_DAYS), end=today)
    daily = {day: tss for day, tss in daily.items() if tss}
    if not daily:
        return None
    day = max(daily, key=daily.get)
    return {"date": day.isoformat(), "tss": round(daily[day], 1)}


def _content_hash(session_date: date, workout_type: str, name: str, steps_json: list) -> str:
    raw = json.dumps(
        [session_date.isoformat(), workout_type, name, steps_json],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


async def _scheduled_on(session: AsyncSession, user: User, day: date) -> list[dict]:
    from app.services.sport_context import _planned_from_plan

    plan = await repo.plan_repo.get_active_plan(session, user.id)
    scheduled = _planned_from_plan(plan, day, day) if plan else []
    for entry in await repo.freestyle_publication_repo.get_active_for_user(session, user.id):
        if entry.session_date == day:
            scheduled.append(
                {
                    "date": day.isoformat(),
                    "workout_type": entry.workout_type,
                    "source": "freestyle",
                    "publication_id": str(entry.id),
                }
            )
    return scheduled


async def evaluate_session(
    session: AsyncSession,
    user: User,
    draft: SessionDraft,
    *,
    today: date | None = None,
) -> dict:
    """What the engine says about this session. Writes nothing.

    Findings describe the athlete's state on `evaluated_as_of`, not a forecast of
    `draft.date`: load on a future day depends on rides that have not happened.
    """
    today = today or paris_today()
    if draft.date < today:
        raise SessionDesignError("the session date is in the past")
    if draft.date > today + timedelta(days=MAX_DAYS_AHEAD):
        raise SessionDesignError(f"the session date is more than {MAX_DAYS_AHEAD} days ahead")

    profile = await load_profile(session, user)
    workout_type, steps, template_id = _resolve_structure(draft, profile)
    bad = sorted({s.zone_code for s in _flat_steps(steps) if not _ZONE_CODE.match(s.zone_code)})
    if bad:
        raise SessionDesignError(f"unknown zone code(s) {bad}: use Z1 to Z6")
    duration = derive_duration_minutes(steps)
    if duration > MAX_SESSION_MINUTES:
        raise SessionDesignError(f"session of {duration} min exceeds {MAX_SESSION_MINUTES} min")

    tss = estimate_structured_session_tss(steps, profile.coaching_mode, profile.equipment.ftp)
    zone = derive_zone_code(steps)
    zones = _zones_for(profile)
    name = (draft.title or "").strip() or render_description(
        workout_type, steps, coaching_mode=profile.coaching_mode
    )

    logs, activities = await load_recent_training(session, user, today)
    inputs = await gather_freestyle_inputs(
        user=user, session=session, logs=logs, activities=activities, today=today
    )
    warnings: list[dict] = []
    reference = None
    fitness = None
    if inputs is None:
        warnings.append({"code": "no_fitness_data"})
    else:
        choice = choose_workout_type(
            inputs.fitness,
            inputs.snapshot,
            days_since_hard_effort=inputs.days_since_hard_effort,
            avoid_workout_types=inputs.avoid_workout_types,
            coaching_mode=profile.coaching_mode,
            ftp=profile.equipment.ftp,
            days_since_return_from_break=inputs.days_since_return_from_break,
            acwr_finding_kind=inputs.acwr_finding_kind,
            recent_daily_tss=inputs.recent_daily_tss,
            recovery_finding_kind=inputs.recovery_finding_kind,
        )
        reference = {"workout_type": choice.workout_type, "target_tss": round(choice.target_tss, 1)}
        current = inputs.current
        fitness = {
            "ctl": inputs.fitness.ctl,
            "atl": inputs.fitness.atl,
            "tsb": inputs.fitness.tsb,
            "source": "intervals.icu" if current else "local_estimate",
            "as_of": current.as_of.isoformat() if current else None,
            "stale": current.is_stale if current else False,
        }
        if current is None or current.is_stale:
            warnings.append({"code": "fitness_not_current"})
        recent_peak = _recent_peak_day(inputs.all_items, today)
        reference["recent_peak_day"] = recent_peak
        # What the athlete has handled lately counts too: the reference is one ordinary day,
        # and a big ride is not a risk signal for someone who rides like that every week.
        within_habits = recent_peak is not None and tss <= recent_peak["tss"]
        if tss > choice.target_tss * REFERENCE_LOAD_MARGIN and not within_habits:
            warnings.append(
                {
                    "code": "above_reference_load",
                    "tss": tss,
                    "reference_tss": reference["target_tss"],
                }
            )
        if workout_type != choice.workout_type:
            warnings.append(
                {
                    "code": "not_the_recommended_type",
                    "recommended": choice.workout_type,
                }
            )
        gap = inputs.days_since_hard_effort
        rest_days = None if gap is None else gap + (draft.date - today).days
        if (
            workout_type == "intervals"
            and rest_days is not None
            and rest_days < HARD_EFFORT_COOLDOWN_DAYS
        ):
            warnings.append({"code": "hard_effort_too_recent", "days": rest_days})
        if workout_type == "intervals" and inputs.days_since_return_from_break is not None:
            warnings.append(
                {
                    "code": "returning_from_break",
                    "days": inputs.days_since_return_from_break,
                }
            )

    findings = await assemble_workload_findings(session, user.id, today=today)
    findings += await assemble_recovery_findings(
        session,
        user.id,
        prescribed_workout_type=workout_type,
        prescribed_zone=zone,
        today=today,
    )
    findings.sort(key=lambda f: f.severity, reverse=True)

    scheduled = await _scheduled_on(session, user, draft.date)
    if scheduled:
        warnings.append({"code": "already_scheduled", "count": len(scheduled)})
    if zones is None:
        warnings.append({"code": "no_threshold_reference"})

    steps_json = _STEPS.dump_python(steps, mode="json")
    return {
        "date": draft.date.isoformat(),
        "evaluated_as_of": today.isoformat(),
        "name": name,
        "workout_type": workout_type,
        "template_id": template_id,
        "duration_minutes": duration,
        "zone_code": zone,
        "tss_estimate": tss,
        "tss_method": f"zone_estimate_{profile.coaching_mode}",
        "steps": steps_json,
        "zone_targets": _targets(steps, zones),
        "publishable": zones is not None,
        "engine_reference": reference,
        "fitness": fitness,
        "guardrails": [_finding(f) for f in findings],
        "warnings": warnings,
        "scheduled_that_day": scheduled,
    }


def _warning_line(warning: dict) -> str:
    values = {k: v for k, v in warning.items() if k != "code"}
    if "recommended" in values:
        values["recommended"] = t(f"session.workout.{values['recommended']}")
    return "⚠️ " + t(f"agent.warning.{warning['code']}", **values)


def proposal_card(evaluation: dict) -> str:
    """The text of the approval card, generated here so it can only state computed values."""
    day = date.fromisoformat(evaluation["date"])
    lines = [
        t("agent.card.header", date=day.strftime("%d/%m/%Y")),
        evaluation["name"],
        t(
            "agent.card.summary",
            minutes=evaluation["duration_minutes"],
            zone=evaluation["zone_code"],
            tss=f"{evaluation['tss_estimate']:.0f}",
        ),
    ]
    for code, target in evaluation["zone_targets"].items():
        band, unit = (target["watts"], "W") if target["watts"] else (target["bpm"], "bpm")
        if band:
            lines.append(f"{code}: {band[0]}–{band[1]} {unit}")
    warnings = [_warning_line(w) for w in evaluation["warnings"]]
    warnings += [
        "⚠️ " + t("agent.warning.guardrail", observed=f["observed"], action=f["action"])
        for f in evaluation["guardrails"]
    ]
    if warnings:
        lines += ["", *warnings]
    return "\n".join(lines)


async def create_proposal(
    session: AsyncSession, user: User, draft: SessionDraft, *, today: date | None = None
) -> dict:
    """Record a session for the athlete to approve. Writes nothing to the calendar."""
    evaluation = await evaluate_session(session, user, draft, today=today)
    if not evaluation["publishable"]:
        raise SessionDesignError(t("agent.warning.no_threshold_reference"), status=409)
    day = date.fromisoformat(evaluation["date"])
    proposal = await repo.proposal_repo.create(
        session,
        user_id=user.id,
        session_date=day,
        workout_type=evaluation["workout_type"],
        name=evaluation["name"][:160],
        steps=evaluation["steps"],
        duration_minutes=evaluation["duration_minutes"],
        tss_estimate=evaluation["tss_estimate"],
        content_hash=_content_hash(
            day, evaluation["workout_type"], evaluation["name"][:160], evaluation["steps"]
        ),
    )
    return {
        **evaluation,
        "proposal_id": proposal.id,
        "card": {
            "text": proposal_card(evaluation),
            "buttons": [[
                {"label": t("agent.card.approve"), "action": f"proposal:approve:{proposal.id}"},
                {"label": t("agent.card.decline"), "action": f"proposal:decline:{proposal.id}"},
            ]],
        },
    }


def _proposal_view(proposal: SessionProposal) -> dict:
    return {
        "proposal_id": proposal.id,
        "status": proposal.status,
        "date": proposal.session_date.isoformat(),
        "name": proposal.name,
        "workout_type": proposal.workout_type,
        "duration_minutes": proposal.duration_minutes,
        "tss_estimate": proposal.tss_estimate,
    }


async def _get_proposal(session: AsyncSession, user: User, proposal_id: str) -> SessionProposal:
    proposal = await repo.proposal_repo.get(session, user.id, proposal_id)
    if proposal is None:
        raise SessionDesignError("unknown proposal", status=404)
    return proposal


async def get_proposal(session: AsyncSession, user: User, proposal_id: str) -> dict:
    return _proposal_view(await _get_proposal(session, user, proposal_id))


async def approve_proposal(
    session: AsyncSession,
    user: User,
    proposal_id: str,
    client: IntervalsClient,
    *,
    today: date | None = None,
) -> dict:
    """The athlete approved: write exactly what was proposed, at most once.

    The claim is committed before the calendar write, so a second tap arriving during the
    write finds nothing to claim. A failed write returns the proposal to pending.
    """
    today = today or paris_today()
    proposal = await _get_proposal(session, user, proposal_id)
    if proposal.status == "published":
        return {
            **_proposal_view(proposal),
            "already_done": True,
            "text": t("agent.card.published_already"),
        }
    if proposal.status != "pending":
        raise SessionDesignError(t("agent.card.no_longer_pending"), status=409)
    if proposal.session_date < today:
        raise SessionDesignError(t("agent.card.date_passed"), status=409)
    if proposal.content_hash != _content_hash(
        proposal.session_date, proposal.workout_type, proposal.name, proposal.steps
    ):
        raise SessionDesignError("the proposal content changed since it was shown", status=409)
    zones = _zones_for(await load_profile(session, user))
    if zones is None:
        raise SessionDesignError(t("agent.warning.no_threshold_reference"), status=409)

    if not await repo.proposal_repo.claim(session, proposal.id):
        raise SessionDesignError(t("agent.card.no_longer_pending"), status=409)
    await session.commit()
    await session.refresh(proposal)

    try:
        outcome, publication_id = await publish_and_record_freestyle(
            session,
            client,
            user_id=user.id,
            session_date=proposal.session_date,
            name=proposal.name,
            workout_type=proposal.workout_type,
            steps=_STEPS.validate_python(proposal.steps),
            zones=zones,
        )
    except Exception:
        await repo.proposal_repo.set_status(session, proposal, "pending")
        await session.commit()
        raise
    if outcome.status != "created":
        await repo.proposal_repo.set_status(session, proposal, "pending")
        await session.commit()
        raise SessionDesignError(
            t("chat.freestyle_publish_failed", detail=outcome.detail or outcome.status),
            status=502,
        )
    await repo.proposal_repo.set_status(
        session, proposal, "published", publication_id=publication_id
    )
    await session.commit()
    return {
        **_proposal_view(proposal),
        "publication_id": str(publication_id),
        "text": t("agent.card.published", date=proposal.session_date.strftime("%d/%m/%Y")),
    }


async def decline_proposal(session: AsyncSession, user: User, proposal_id: str) -> dict:
    proposal = await _get_proposal(session, user, proposal_id)
    if proposal.status != "pending":
        raise SessionDesignError(t("agent.card.no_longer_pending"), status=409)
    await repo.proposal_repo.set_status(session, proposal, "declined")
    await session.commit()
    return {**_proposal_view(proposal), "text": t("agent.card.declined")}


async def list_publications(session: AsyncSession, user: User) -> list[dict]:
    entries = await repo.freestyle_publication_repo.get_active_for_user(session, user.id)
    return [
        {
            "publication_id": str(e.id),
            "date": e.session_date.isoformat(),
            "workout_type": e.workout_type,
        }
        for e in sorted(entries, key=lambda e: e.session_date)
    ]


async def withdraw_publication(
    session: AsyncSession, user: User, publication_id: uuid.UUID, client: IntervalsClient
) -> dict:
    from app.providers.intervals.calendar import withdraw_event

    entry = await repo.freestyle_publication_repo.get_active_by_id(session, user.id, publication_id)
    if entry is None:
        raise SessionDesignError("no active publication with this id", status=404)
    await withdraw_event(client, entry.intervals_event_id)
    await repo.freestyle_publication_repo.mark_withdrawn(session, entry.id)
    await session.commit()
    return {
        "publication_id": str(entry.id),
        "text": t("agent.card.withdrawn", date=entry.session_date.strftime("%d/%m/%Y")),
    }


async def withdrawal_card(session: AsyncSession, user: User, publication_id: uuid.UUID) -> dict:
    entry = await repo.freestyle_publication_repo.get_active_by_id(session, user.id, publication_id)
    if entry is None:
        raise SessionDesignError("no active publication with this id", status=404)
    return {
        "publication_id": str(entry.id),
        "card": {
            "text": t(
                "agent.card.withdraw_question",
                date=entry.session_date.strftime("%d/%m/%Y"),
                workout=t(f"session.workout.{entry.workout_type}"),
            ),
            "buttons": [[
                {"label": t("agent.card.withdraw"), "action": f"publication:withdraw:{entry.id}"},
                {"label": t("agent.card.keep"), "action": "dismiss:agent.card.kept"},
            ]],
        },
    }
