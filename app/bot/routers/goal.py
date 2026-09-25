"""Router : /goal — spec 007 US3.

Changer d'objectif sans effacer l'athlète : relit la source en silence, demande
objectif + date, régénère le plan depuis la forme actuelle, garde tout l'historique,
signale un calendrier périmé (FR-011..FR-016).
"""
from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import GoalStates, PlanStates
from app.config import settings
from app.core.localization import t
from app.db import repositories as repo
from app.engine.plan_builder import generate_plan
from app.engine.schemas import TrainingPlanSchema
from app.providers.intervals.athlete_profile import read_athlete_profile, stamp
from app.providers.intervals.client import IntervalsClient
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)
router = Router(name="goal")
# MARKER_TEST_12345


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


def _confirm_regen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("goal.confirm_button"), callback_data="goal:confirm:apply"),
        InlineKeyboardButton(text=t("goal.cancel_button"), callback_data="goal:confirm:cancel"),
    ]])


def _confirm_freestyle_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=t("goal.enter_freestyle_button"), callback_data="goal:freestyle:apply"
        ),
        InlineKeyboardButton(
            text=t("goal.cancel_freestyle_button"), callback_data="goal:freestyle:cancel"
        ),
    ]])


def _goal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("goal.type_event"), callback_data="goal:type:event")],
        [InlineKeyboardButton(text=t("goal.type_fitness"), callback_data="goal:type:fitness")],
        [InlineKeyboardButton(
            text=t("goal.type_performance"), callback_data="goal:type:performance"
        )],
        [InlineKeyboardButton(text=t("goal.type_other"), callback_data="goal:type:other")],
        [InlineKeyboardButton(
            text=t("goal.type_freestyle"), callback_data="goal:type:freestyle"
        )],
    ])


@router.message(Command("goal"))
async def cmd_goal(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    """spec 009 : plus de blocage sans plan actif — c'est aussi le point d'entrée pour
    passer du mode libre au mode objectif (research.md Decision 2)."""
    if user is None or not user.onboarding_completed:
        await message.answer(t("goal.setup_first"))
        return
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    if profile_row is None:
        await message.answer(t("goal.profile_missing"))
        return

    try:
        fresh = await read_athlete_profile(_client())
    except Exception:
        logger.exception("read_athlete_profile failed during /goal")
        await message.answer(t("goal.profile_read_failed"))
        return

    plan = await repo.plan_repo.get_active_plan(session, user.id)
    await state.clear()
    await state.update_data(
        _old_plan_id=str(plan.id) if plan else None,
        _fresh_source_profile={
            "ftp": fresh.ftp.value,
            "max_hr": fresh.max_hr.value,
            "resting_hr": fresh.resting_hr.value,
            "age": fresh.age.value,
            "coaching_mode": fresh.coaching_mode,
            "read_from_source_at": stamp(),
        },
    )
    await state.set_state(GoalStates.GOAL)
    await message.answer(
        t("goal.choose_goal"),
        reply_markup=_goal_kb(),
    )


@router.callback_query(GoalStates.GOAL, F.data.startswith("goal:type:"))
async def goal_type(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    goal = callback.data.split(":")[2]

    if goal == "freestyle":
        plan = await repo.plan_repo.get_active_plan(session, user.id)
        if plan is None:
            await _enter_freestyle_mode(callback, state, session, user)
            return
        entries = await repo.publication_repo.get_active_entries_for_plan(
            session, user.id, plan.id
        )
        await state.set_state(GoalStates.CONFIRM_FREESTYLE)
        await callback.message.edit_text(
            t("goal.freestyle_preview", count=len(entries)),
            reply_markup=_confirm_freestyle_kb(),
        )
        await callback.answer()
        return

    await state.update_data(goal=goal)
    await state.set_state(GoalStates.DATE)
    await callback.message.edit_text(
        t("goal.date_prompt"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(GoalStates.CONFIRM_FREESTYLE, F.data == "goal:freestyle:apply")
async def confirm_freestyle_mode(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    await _enter_freestyle_mode(callback, state, session, user)


@router.callback_query(GoalStates.CONFIRM_FREESTYLE, F.data == "goal:freestyle:cancel")
async def cancel_freestyle_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PlanStates.ACTIVE)
    await callback.message.edit_text(t("goal.freestyle_cancelled"))
    await callback.answer()


async def _enter_freestyle_mode(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    """spec 009 US2 — bascule objectif → libre. Idempotent (FR : pas d'erreur si déjà en
    mode libre) ; retire automatiquement le calendrier publié (FR-013/contracts/
    mode-switch-interaction.md), ne touche à rien d'autre (FR-012)."""
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await state.clear()
        await callback.message.edit_text(t("goal.already_freestyle"))
        await callback.answer()
        return

    # Calendar removal is an external mutation. Complete it before changing local mode:
    # a partial failure must leave the active plan reachable for a safe retry.
    try:
        from app.services.publication import withdraw_all_publications

        withdrawn, failed = await withdraw_all_publications(session, _client(), user, plan)
    except Exception:
        logger.warning("calendar withdrawal failed before entering freestyle mode")
        await state.clear()
        await state.set_state(PlanStates.ACTIVE)
        await callback.message.edit_text(t("goal.calendar_unavailable"))
        await callback.answer()
        return
    if failed:
        await state.clear()
        await state.set_state(PlanStates.ACTIVE)
        await callback.message.edit_text(
            t("goal.withdraw_failed", withdrawn=withdrawn, failed=failed)
        )
        await callback.answer()
        return

    confirmed_calendar_note = (
        t("goal.withdrawn_note", count=withdrawn)
        if withdrawn else ""
    )
    await repo.plan_repo.deactivate_all_for_user(session, user.id)

    from app.db.repositories import journal_repo

    await journal_repo.create(
        session,
        user_id=user.id,
        entry_date=date.today(),
        category="freestyle_toggle",
        source="deterministic",
        text=t("goal.journal_enter_freestyle"),
    )

    calendar_note = confirmed_calendar_note
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    summary = t("goal.freestyle_activated", calendar_note=calendar_note, logs=len(logs))
    await state.clear()
    await callback.message.edit_text(summary, parse_mode="HTML")
    await callback.answer()


@router.message(GoalStates.DATE, F.text)
async def goal_date(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    from app.bot.routers.setup import parse_goal_date

    target_date, problem = parse_goal_date(message.text)
    if problem == "format":
        await message.answer(t("goal.date_invalid_format"),
                             parse_mode="HTML")
        return
    if problem == "past":
        await message.answer(t("goal.date_in_past"))
        return
    data = await state.get_data()
    if problem == "too_soon" and data.get("_date_confirmed") != target_date.isoformat():
        await state.update_data(_date_confirmed=target_date.isoformat())
        await message.answer(t("goal.date_too_soon", days=(target_date - date.today()).days))
        return
    if problem == "too_far":
        await message.answer(t("goal.date_too_far"))

    # spec 007 US3 (revu) : un plan actif existe → on montre un résumé et on attend
    # confirmation avant d'écraser quoi que ce soit. Venue du mode libre (aucun plan
    # actif) → comportement inchangé, exécution immédiate (rien à perdre).
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if old_plan is None:
        await message.answer(t("goal.regenerating"))
        await _regenerate(
            message, state, session, user, data["goal"], target_date,
            data.get("_fresh_source_profile"),
        )
        return

    await message.answer(t("goal.preparing_preview"))
    new_plan, profile = await _build_new_plan(
        session, user, data["goal"], target_date, data.get("_fresh_source_profile")
    )
    old_schema = TrainingPlanSchema.model_validate(old_plan.plan_technical)

    await state.update_data(
        _pending_goal=data["goal"],
        _pending_target_date=target_date.isoformat() if target_date else None,
        _pending_new_plan=new_plan.model_dump(mode="json"),
        _pending_profile=profile.model_dump(mode="json"),
    )
    await state.set_state(GoalStates.CONFIRM_REGEN)
    date_str = t("goal.preview_date", date=target_date.strftime("%d/%m/%Y")) if target_date else ""
    await message.answer(_plan_change_preview(old_schema, new_plan), parse_mode="HTML")
    await message.answer(
        t("goal.new_plan_preview", weeks=new_plan.weeks_count,
          current_weeks=old_schema.weeks_count, goal=data["goal"], date_str=date_str),
        reply_markup=_confirm_regen_kb(),
        parse_mode="HTML",
    )


@router.callback_query(GoalStates.CONFIRM_REGEN, F.data == "goal:confirm:apply")
async def goal_confirm_apply(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    from app.engine.schemas import AthleteProfileSchema

    data = await state.get_data()
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    new_plan = TrainingPlanSchema.model_validate(data["_pending_new_plan"])
    profile = AthleteProfileSchema.model_validate(data["_pending_profile"])
    target_date = (
        date.fromisoformat(data["_pending_target_date"])
        if data.get("_pending_target_date") else None
    )

    await callback.answer(t("goal.applying_callback"))
    await callback.message.edit_text(t("goal.applying"))
    await _apply_new_plan(
        callback.message, state, session, user, data["_pending_goal"], target_date,
        new_plan, profile, old_plan,
    )


@router.callback_query(GoalStates.CONFIRM_REGEN, F.data == "goal:confirm:cancel")
async def goal_confirm_cancel(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    await state.clear()
    await state.set_state(PlanStates.ACTIVE)
    await callback.message.edit_text(t("goal.regen_cancelled"))
    await callback.answer()


def _plan_change_preview(old: TrainingPlanSchema, new: TrainingPlanSchema) -> str:
    """Small, deterministic comparison shown before a plan is replaced."""
    old_first = old.weeks[0] if old.weeks else None
    new_first = new.weeks[0] if new.weeks else None
    old_tss = old_first.total_tss_target if old_first else 0
    new_tss = new_first.total_tss_target if new_first else 0
    old_sessions = len(old_first.sessions) if old_first else 0
    new_sessions = len(new_first.sessions) if new_first else 0
    return t("goal.plan_change_preview", old_sessions=old_sessions, old_tss=old_tss,
             new_sessions=new_sessions, new_tss=new_tss,
             old_peak=old.peak_weekly_tss, new_peak=new.peak_weekly_tss)


async def _build_new_plan(
    session, user, goal: str, target_date: date | None, fresh_source: dict | None = None,
):
    """Partie pure de la régénération — aucune écriture DB. Lit le profil et la forme
    actuelle, construit et retourne le nouveau plan + profil, pour aperçu (confirmation)
    ou application immédiate (_regenerate) selon l'appelant."""
    from app.engine.schemas import AthleteProfileSchema

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
    new_plan = generate_plan(profile)
    return new_plan, profile


async def _regenerate(
    message, state, session, user, goal: str, target_date: date | None,
    fresh_source: dict | None = None,
) -> None:
    # spec 009 : old_plan is None when this is entered from freestyle mode (no plan to
    # diff against) — every use below is guarded accordingly. Kept as a thin wrapper
    # around _build_new_plan()/_apply_new_plan() so callers that need the whole thing
    # done in one shot (freestyle→goal, and every existing test) see no behavior change.
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    new_plan, profile = await _build_new_plan(session, user, goal, target_date, fresh_source)
    await _apply_new_plan(
        message, state, session, user, goal, target_date, new_plan, profile, old_plan
    )


async def _apply_new_plan(
    message, state, session, user, goal: str, target_date: date | None,
    new_plan, profile, old_plan,
) -> None:
    """Partie écriture — appelée soit directement (_regenerate, pas de confirmation
    nécessaire), soit après confirmation explicite (goal_confirm_apply)."""
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    old_schema = (
        TrainingPlanSchema.model_validate(old_plan.plan_technical) if old_plan else None
    )

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
    # d'objectif au sein du mode objectif (goal_change) — un seul événement par clic,
    # pas les deux.
    from app.db.repositories import journal_repo

    date_str = (
        t("goal.journal_target_date", date=target_date.strftime("%d/%m/%Y"))
        if target_date else ""
    )
    if old_schema is None:
        await journal_repo.create(
            session,
            user_id=user.id,
            entry_date=date.today(),
            category="freestyle_toggle",
            source="deterministic",
            text=t("goal.journal_leave_freestyle", goal=goal, date_str=date_str,
                   weeks=new_plan.weeks_count),
        )
    else:
        await journal_repo.create(
            session,
            user_id=user.id,
            entry_date=date.today(),
            category="goal_change",
            source="deterministic",
            text=t("goal.journal_goal_changed", goal=goal, date_str=date_str,
                   old_weeks=old_schema.weeks_count, new_weeks=new_plan.weeks_count),
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
                session, _client(), user
            )
            if withdrawn:
                freestyle_withdrawn_note = t("goal.freestyle_withdrawn_note", count=withdrawn)
        except Exception:
            logger.warning("retrait des publications mode libre impossible après /goal")

    # Ce qui change / ce qui est gardé (FR-016). "Ce qui change" n'a de sens que s'il y
    # avait un ancien plan à comparer (spec 009 : entrée depuis mode libre = rien à diffé).
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    change_note = (
        t("goal.change_note", old_weeks=old_schema.weeks_count,
          new_weeks=new_plan.weeks_count, ctl=new_plan.initial_weekly_tss / 7)
        if old_schema is not None else ""
    )
    goal_part = (
        t("goal.summary_dated_goal", goal=goal, date=target_date.strftime("%d/%m/%Y"))
        if target_date else t("goal.summary_undated_goal", goal=goal)
    )
    summary = t("goal.summary", weeks=new_plan.weeks_count, goal_part=goal_part,
                change_note=change_note, logs=len(logs), stale_note=stale_note,
                freestyle_withdrawn_note=freestyle_withdrawn_note)
    await state.clear()
    await state.set_state(PlanStates.ACTIVE)
    await message.answer(summary, parse_mode="HTML")

