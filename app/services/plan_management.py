"""Changing the training plan: a new goal, leaving the plan, adjusting it, publishing it.

Used by the bot's `/goal` flow and by the agent API. An agent's proposal is stored and bound to
the plan it was computed from; only `apply_change()`, on the athlete's gesture, changes anything.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.localization import t
from app.db import repositories as repo
from app.db.models.publication import PlanChangeProposal
from app.db.models.user import User
from app.engine.plan_builder import generate_plan
from app.engine.plan_modifier import (
    apply_proposed_modification,
    apply_session_adjustment,
    propose_session_adjustment,
    propose_week_adjustment,
)
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], object]
MIN_WEEKS_NOTICE_DAYS = 21


class PlanChangeError(ValueError):
    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


async def read_fresh_source(client) -> dict:
    """What the source says now: a new plan is built on the current thresholds (spec 007)."""
    from app.providers.intervals.athlete_profile import read_athlete_profile, stamp

    fresh = await read_athlete_profile(client)
    return {
        "ftp": fresh.ftp.value,
        "max_hr": fresh.max_hr.value,
        "resting_hr": fresh.resting_hr.value,
        "age": fresh.age.value,
        "coaching_mode": fresh.coaching_mode,
        "read_from_source_at": stamp(),
    }


# ── New goal ─────────────────────────────────────────────────────────────────


def plan_change_preview(old: TrainingPlanSchema, new: TrainingPlanSchema) -> str:
    """Small, deterministic comparison shown before a plan is replaced."""
    old_first = old.weeks[0] if old.weeks else None
    new_first = new.weeks[0] if new.weeks else None
    return t(
        "goal.plan_change_preview",
        old_sessions=len(old_first.sessions) if old_first else 0,
        old_tss=old_first.total_tss_target if old_first else 0,
        new_sessions=len(new_first.sessions) if new_first else 0,
        new_tss=new_first.total_tss_target if new_first else 0,
        old_peak=old.peak_weekly_tss,
        new_peak=new.peak_weekly_tss,
    )


async def build_new_plan(
    session,
    user,
    goal: str,
    target_date: date | None,
    fresh_source: dict | None = None,
):
    """Partie pure de la régénération — aucune écriture DB. Lit le profil et la forme
    actuelle, construit et retourne le nouveau plan + profil."""
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)

    # Forme actuelle — jamais un départ à zéro (FR-012).
    current = await get_current_fitness(session, user.id)
    fitness = current.metrics if current is not None else None

    # Profil inchangé sauf l'objectif : on ne re-demande rien (FR-013).
    pdata = dict(profile_row.profile)
    if fresh_source is not None:
        equipment = dict(pdata["equipment"])
        physio = dict(pdata["physio"])
        ftp = fresh_source.get("ftp")
        equipment.update(
            power_meter=ftp is not None,
            ftp=int(ftp) if ftp is not None else None,
            ftp_source="source" if ftp is not None else "estimated",
        )
        for key, source_key, origin_key in (
            ("hr_max", "max_hr", "hr_max_source"),
            ("hr_rest", "resting_hr", "hr_rest_source"),
        ):
            value = fresh_source.get(source_key)
            if value is not None:
                physio[key] = int(value)
                physio[origin_key] = "source"
        if fresh_source.get("age") is not None:
            physio["age"] = int(fresh_source["age"])
        pdata["equipment"] = equipment
        pdata["physio"] = physio
        pdata["coaching_mode"] = fresh_source["coaching_mode"]
    pdata["objective"] = {
        "type": goal,
        "target_date": target_date.isoformat() if target_date else None,
    }
    if fitness is not None:
        pdata["current_ctl"] = fitness.ctl
        pdata["current_atl"] = fitness.atl
        pdata["current_tsb"] = fitness.tsb

    profile = AthleteProfileSchema.model_validate(pdata)
    return generate_plan(profile), profile


async def apply_new_plan(
    session,
    user,
    client_factory: ClientFactory,
    goal: str,
    target_date: date | None,
    new_plan,
    profile,
    old_plan,
) -> str:
    """Write the new plan and return the athlete-facing summary."""
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    old_schema = TrainingPlanSchema.model_validate(old_plan.plan_technical) if old_plan else None

    await repo.plan_repo.deactivate_all_for_user(session, user.id)
    await repo.plan_repo.create(
        session=session,
        user_id=user.id,
        plan_technical=new_plan.model_dump(mode="json"),
        start_date=new_plan.start_date or date.today(),
        end_date=new_plan.end_date or date.today(),
    )
    await repo.profile_repo.update(session, profile_row, profile.model_dump(mode="json"))

    # Journal daté (Enduragent parity review, 2026-09-20) — texte templaté depuis des
    # variables déjà calculées, jamais de prose libre (source="deterministic"). Venir du
    # mode libre est une bascule de mode (freestyle_toggle), pas un simple changement
    # d'objectif au sein du mode objectif (goal_change) — un seul événement par clic.
    date_str = (
        t("goal.journal_target_date", date=target_date.strftime("%d/%m/%Y")) if target_date else ""
    )
    if old_schema is None:
        await repo.journal_repo.create(
            session,
            user_id=user.id,
            entry_date=date.today(),
            category="freestyle_toggle",
            source="deterministic",
            text=t(
                "goal.journal_leave_freestyle",
                goal=goal,
                date_str=date_str,
                weeks=new_plan.weeks_count,
            ),
        )
    else:
        await repo.journal_repo.create(
            session,
            user_id=user.id,
            entry_date=date.today(),
            category="goal_change",
            source="deterministic",
            text=t(
                "goal.journal_goal_changed",
                goal=goal,
                date_str=date_str,
                old_weeks=old_schema.weeks_count,
                new_weeks=new_plan.weeks_count,
            ),
        )

    # Calendrier publié sous l'ancien plan → périmé (FR-014, réutilise spec 005). Rien à
    # vérifier si on vient du mode libre (aucun ancien plan, donc rien de publié).
    stale_note = ""
    if old_plan is not None:
        try:
            entries = await repo.publication_repo.get_active_entries_for_plan(
                session, user.id, old_plan.id
            )
            if entries:
                stale_note = t("goal.stale_calendar_note", count=len(entries))
        except Exception:
            logger.warning("check calendrier périmé impossible après /goal")

    # spec 010 FR-011 : venir du mode libre retire aussi les séances mode libre encore
    # publiées, symétrique du retrait des séances de plan à l'entrée en mode libre
    # (spec 009 FR-013) — jamais les deux types de séances périmées à la fois.
    freestyle_withdrawn_note = ""
    if old_plan is None:
        try:
            from app.services.publication import withdraw_freestyle_publications

            withdrawn, _failed = await withdraw_freestyle_publications(
                session, client_factory(), user
            )
            if withdrawn:
                freestyle_withdrawn_note = t("goal.freestyle_withdrawn_note", count=withdrawn)
        except Exception:
            logger.warning("retrait des publications mode libre impossible après /goal")

    # Ce qui change / ce qui est gardé (FR-016). "Ce qui change" n'a de sens que s'il y
    # avait un ancien plan à comparer (spec 009 : entrée depuis mode libre = rien à diffé).
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    change_note = (
        t(
            "goal.change_note",
            old_weeks=old_schema.weeks_count,
            new_weeks=new_plan.weeks_count,
            ctl=new_plan.initial_weekly_tss / 7,
        )
        if old_schema is not None
        else ""
    )
    goal_part = (
        t("goal.summary_dated_goal", goal=goal, date=target_date.strftime("%d/%m/%Y"))
        if target_date
        else t("goal.summary_undated_goal", goal=goal)
    )
    return t(
        "goal.summary",
        weeks=new_plan.weeks_count,
        goal_part=goal_part,
        change_note=change_note,
        logs=len(logs),
        stale_note=stale_note,
        freestyle_withdrawn_note=freestyle_withdrawn_note,
    )


async def enter_freestyle(session, user, client_factory: ClientFactory) -> tuple[bool, str]:
    """spec 009 US2 — bascule objectif → libre. `(done, text)`. Idempotent (FR : pas d'erreur si
    déjà en mode libre) ; retire le calendrier publié (FR-013), ne touche à rien d'autre."""
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        return True, t("goal.already_freestyle")

    # Calendar removal is an external mutation. Complete it before changing local mode:
    # a partial failure must leave the active plan reachable for a safe retry.
    try:
        from app.services.publication import withdraw_all_publications

        withdrawn, failed = await withdraw_all_publications(session, client_factory(), user, plan)
    except Exception:
        logger.warning("calendar withdrawal failed before entering freestyle mode")
        return False, t("goal.calendar_unavailable")
    if failed:
        return False, t("goal.withdraw_failed", withdrawn=withdrawn, failed=failed)

    calendar_note = t("goal.withdrawn_note", count=withdrawn) if withdrawn else ""
    await repo.plan_repo.deactivate_all_for_user(session, user.id)
    await repo.journal_repo.create(
        session,
        user_id=user.id,
        entry_date=date.today(),
        category="freestyle_toggle",
        source="deterministic",
        text=t("goal.journal_enter_freestyle"),
    )
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    return True, t("goal.freestyle_activated", calendar_note=calendar_note, logs=len(logs))


# ── Proposals from an agent ──────────────────────────────────────────────────


def _jsonable(value: dict) -> dict:
    return json.loads(json.dumps(value, default=str))


async def _store(session, user, kind: str, plan, payload: dict) -> PlanChangeProposal:
    proposal = PlanChangeProposal(
        id=uuid.uuid4().hex[:12],
        user_id=user.id,
        kind=kind,
        based_on_plan_id=plan.id if plan else None,
        payload=_jsonable(payload),
        status="pending",
        created_at=datetime.now(UTC),
    )
    session.add(proposal)
    await session.flush()
    return proposal


def _card(proposal: PlanChangeProposal, text: str, apply_label: str) -> dict:
    return {
        "proposal_id": proposal.id,
        "card": {
            "text": text,
            "buttons": [
                [
                    {"label": apply_label, "action": f"change:apply:{proposal.id}"},
                    {
                        "label": t("agent.card.change_cancel"),
                        "action": f"change:cancel:{proposal.id}",
                    },
                ]
            ],
        },
    }


def _goal_label(goal: str) -> str:
    return t(f"llm.tools_goal.{goal}")


async def propose_new_plan(
    session: AsyncSession, user: User, goal: str, target_date: date | None, client
) -> dict:
    today = date.today()
    if target_date is not None and target_date <= today:
        raise PlanChangeError("the target date must be in the future")
    if await repo.profile_repo.get_by_user_id(session, user.id) is None:
        raise PlanChangeError("the athlete has no profile yet", status=409)
    try:
        fresh = await read_fresh_source(client)
    except Exception as exc:
        logger.exception("read_athlete_profile failed while proposing a plan")
        raise PlanChangeError(t("goal.profile_read_failed"), status=502) from exc
    new_plan, profile = await build_new_plan(session, user, goal, target_date, fresh)

    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    preview = ""
    if old_plan is not None:
        preview = plan_change_preview(
            TrainingPlanSchema.model_validate(old_plan.plan_technical), new_plan
        )
    first = new_plan.weeks[0] if new_plan.weeks else None
    text = t(
        "agent.card.plan_new",
        goal=_goal_label(goal),
        weeks=new_plan.weeks_count,
        date_part=(
            t("agent.card.plan_new_date", date=target_date.strftime("%d/%m/%Y"))
            if target_date
            else ""
        ),
        preview=preview,
    )
    proposal = await _store(
        session,
        user,
        "new_plan",
        old_plan,
        {
            "goal": goal,
            "target_date": target_date.isoformat() if target_date else None,
            "plan": new_plan.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
        },
    )
    warnings = []
    if target_date is not None and (target_date - today).days < MIN_WEEKS_NOTICE_DAYS:
        warnings.append({"code": "target_date_soon", "days": (target_date - today).days})
    return {
        **_card(proposal, text, t("agent.card.plan_apply")),
        "weeks": new_plan.weeks_count,
        "first_week_tss": first.total_tss_target if first else None,
        "peak_weekly_tss": new_plan.peak_weekly_tss,
        "warnings": warnings,
    }


async def propose_leaving_plan(session: AsyncSession, user: User) -> dict:
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        raise PlanChangeError(t("goal.already_freestyle"), status=409)
    proposal = await _store(session, user, "leave_plan", plan, {})
    return _card(proposal, t("agent.card.leave_plan"), t("agent.card.leave_plan_apply"))


async def _active_plan_or_fail(session, user):
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        raise PlanChangeError(t("llm.tools.no_plan_to_modify"), status=409)
    return plan


async def propose_week_change(
    session: AsyncSession, user: User, *, reason: str, modification_type: str, week_offset: int
) -> dict:
    plan = await _active_plan_or_fail(session, user)
    result = propose_week_adjustment(plan, week_offset, modification_type)
    if "error" in result:
        raise PlanChangeError(str(result["error"]))
    result["reason"] = reason
    proposal = await _store(session, user, "week_adjustment", plan, result)
    text = str(result.get("summary") or t("chat.session_modified_default"))
    return {**_card(proposal, text, t("agent.card.change_apply")), "change": _jsonable(result)}


async def propose_session_change(
    session: AsyncSession, user: User, *, constraint_type: str, day_offset: int, action: str
) -> dict:
    plan = await _active_plan_or_fail(session, user)
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    availability = ((row.profile or {}).get("availability") or {}) if row else {}
    result = propose_session_adjustment(
        plan,
        date.today() + timedelta(days=day_offset),
        action,
        availability.get("preferred_days") or [],
    )
    if "error" in result:
        raise PlanChangeError(str(result["error"]))
    result["constraint_type"] = constraint_type
    proposal = await _store(session, user, "session_adjustment", plan, result)
    text = str(result.get("summary") or t("chat.session_modified_default"))
    return {**_card(proposal, text, t("agent.card.change_apply")), "change": _jsonable(result)}


async def _get_pending(session, user, proposal_id: str) -> PlanChangeProposal:
    result = await session.execute(
        select(PlanChangeProposal).where(
            PlanChangeProposal.id == proposal_id, PlanChangeProposal.user_id == user.id
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise PlanChangeError("unknown proposal", status=404)
    if proposal.status != "pending":
        raise PlanChangeError(t("agent.card.no_longer_pending"), status=409)
    return proposal


def _close(proposal: PlanChangeProposal, status: str) -> None:
    proposal.status = status
    proposal.decided_at = datetime.now(UTC)


async def cancel_change(session: AsyncSession, user: User, proposal_id: str) -> str:
    proposal = await _get_pending(session, user, proposal_id)
    _close(proposal, "declined")
    await session.commit()
    return t("agent.card.change_cancelled")


async def apply_change(
    session: AsyncSession, user: User, proposal_id: str, client_factory: ClientFactory
) -> str:
    """The athlete approved. Refused if the plan moved since the proposal was computed."""
    proposal = await _get_pending(session, user, proposal_id)
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if (plan.id if plan else None) != proposal.based_on_plan_id:
        _close(proposal, "stale")
        await session.commit()
        raise PlanChangeError(t("agent.card.change_stale"), status=409)

    if proposal.kind == "new_plan":
        data = proposal.payload
        target = data.get("target_date")
        text = await apply_new_plan(
            session,
            user,
            client_factory,
            data["goal"],
            date.fromisoformat(target) if target else None,
            TrainingPlanSchema.model_validate(data["plan"]),
            AthleteProfileSchema.model_validate(data["profile"]),
            plan,
        )
    elif proposal.kind == "leave_plan":
        done, text = await enter_freestyle(session, user, client_factory)
        if not done:
            # Nothing was switched: the card stays open, a tap can retry.
            await session.commit()
            raise PlanChangeError(text, status=502)
    elif proposal.kind == "session_adjustment":
        if not apply_session_adjustment(plan, dict(proposal.payload)):
            raise PlanChangeError(t("chat.modification_failed"), status=409)
        flag_modified(plan, "plan_technical")
        text = t(
            "chat.session_updated",
            summary=proposal.payload.get("summary", t("chat.session_modified_default")),
        )
    else:
        if not apply_proposed_modification(plan, dict(proposal.payload)):
            raise PlanChangeError(t("chat.modification_failed"), status=409)
        flag_modified(plan, "plan_technical")
        text = t(
            "chat.plan_updated",
            week_num=proposal.payload.get("week_number", "?"),
            tss_after=proposal.payload.get("tss_after", "?"),
        )
    _close(proposal, "applied")
    await session.commit()
    return text


# ── Reading the plan ─────────────────────────────────────────────────────────


def current_week_number(schema: TrainingPlanSchema, start: date, today: date) -> int:
    return max(1, min(schema.weeks_count, (today - start).days // 7 + 1))


async def plan_view(
    session: AsyncSession, user: User, *, week: int | None = None, today: date | None = None
) -> dict:
    today = today or date.today()
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        return {"mode": "freestyle", "plan": None}
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    current = current_week_number(schema, plan.start_date, today)
    selected = next((w for w in schema.weeks if w.week_number == (week or current)), None)
    if selected is None:
        raise PlanChangeError(f"no week {week}: the plan has {schema.weeks_count} weeks")
    days = {}
    for item in selected.sessions:
        day = plan.start_date + timedelta(weeks=selected.week_number - 1, days=item.day_of_week)
        days[day.isoformat()] = item.model_dump(mode="json")
    return {
        "mode": "goal",
        "plan": {
            "start_date": plan.start_date.isoformat(),
            "end_date": plan.end_date.isoformat(),
            "weeks_count": schema.weeks_count,
            "current_week": current,
            "peak_weekly_tss": schema.peak_weekly_tss,
            "weeks": [
                {
                    "week_number": w.week_number,
                    "phase": w.phase,
                    "total_tss_target": w.total_tss_target,
                    "sessions": len(w.sessions),
                }
                for w in schema.weeks
            ],
        },
        "week": {
            "week_number": selected.week_number,
            "phase": selected.phase,
            "total_tss_target": selected.total_tss_target,
            "sessions_by_date": days,
        },
    }
