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
from app.db import repositories as repo
from app.engine.plan_builder import generate_plan
from app.engine.schemas import TrainingPlanSchema
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
        InlineKeyboardButton(text="✅ Confirmer", callback_data="goal:confirm:apply"),
        InlineKeyboardButton(text="❌ Annuler", callback_data="goal:confirm:cancel"),
    ]])


def _goal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Événement cible", callback_data="goal:type:event")],
        [InlineKeyboardButton(text="💚 Forme générale", callback_data="goal:type:fitness")],
        [InlineKeyboardButton(text="⚡ Performance", callback_data="goal:type:performance")],
        [InlineKeyboardButton(text="🔹 Autre", callback_data="goal:type:other")],
        [InlineKeyboardButton(
            text="🚴 Pas d'objectif / mode libre", callback_data="goal:type:freestyle"
        )],
    ])


@router.message(Command("goal"))
async def cmd_goal(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    """spec 009 : plus de blocage sans plan actif — c'est aussi le point d'entrée pour
    passer du mode libre au mode objectif (research.md Decision 2)."""
    if user is None or not user.onboarding_completed:
        await message.answer("Fais d'abord /setup.")
        return
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    if profile_row is None:
        await message.answer("Profil introuvable — relance /setup.")
        return

    plan = await repo.plan_repo.get_active_plan(session, user.id)
    await state.clear()
    await state.update_data(_old_plan_id=str(plan.id) if plan else None)
    await state.set_state(GoalStates.GOAL)
    await message.answer(
        "On change d'objectif. Je garde tout le reste — tes séances faites, ton "
        "historique, notre conversation.\n\nNouvel objectif ?",
        reply_markup=_goal_kb(),
    )


@router.callback_query(GoalStates.GOAL, F.data.startswith("goal:type:"))
async def goal_type(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    goal = callback.data.split(":")[2]

    if goal == "freestyle":
        await _enter_freestyle_mode(callback, state, session, user)
        return

    await state.update_data(goal=goal)
    await state.set_state(GoalStates.DATE)
    await callback.message.edit_text(
        "📅 Date de l'objectif ? Format <code>AAAA-MM-JJ</code>, ou <code>aucune</code>.",
        parse_mode="HTML",
    )
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
        await callback.message.edit_text("Déjà en mode libre.")
        await callback.answer()
        return

    await repo.plan_repo.deactivate_all_for_user(session, user.id)

    withdrawn = 0
    calendar_note = ""
    try:
        from app.services.publication import withdraw_all_publications

        withdrawn, failed = await withdraw_all_publications(session, _client(), user, plan)
        if withdrawn:
            calendar_note = (
                f"\n📅 {withdrawn} séance(s) retirée(s) de ton calendrier intervals.icu "
                "(elles ne correspondaient plus à rien)."
            )
        if failed:
            calendar_note += f"\n⚠️ {failed} retrait(s) ont échoué — relance /unpublish si besoin."
    except Exception:
        logger.warning("Retrait du calendrier impossible en passant en mode libre")

    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    summary = (
        "🚴 <b>Mode libre activé</b>\n\n"
        "Je ne suis plus de plan — demande-moi une séance quand tu veux, je m'adapte à "
        "ta forme du moment."
        f"{calendar_note}"
        f"\n\nCe qui est gardé : {len(logs)} séances loggées, ton historique, tes "
        "réglages — rien n'a bougé."
    )
    await state.clear()
    await callback.message.edit_text(summary, parse_mode="HTML")
    await callback.answer()


@router.message(GoalStates.DATE, F.text)
async def goal_date(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    from app.bot.routers.setup import parse_goal_date

    target_date, problem = parse_goal_date(message.text)
    if problem == "format":
        await message.answer("⚠️ Format <code>AAAA-MM-JJ</code> ou <code>aucune</code>.",
                             parse_mode="HTML")
        return
    if problem == "past":
        await message.answer("⚠️ La date doit être dans le futur.")
        return
    data = await state.get_data()
    if problem == "too_soon" and data.get("_date_confirmed") != target_date.isoformat():
        await state.update_data(_date_confirmed=target_date.isoformat())
        await message.answer(
            f"⚠️ {(target_date - date.today()).days} jours, c'est court. "
            "Renvoie la même date pour confirmer, ou une plus lointaine."
        )
        return
    if problem == "too_far":
        await message.answer(
            "ℹ️ Si loin, le plan est surtout spéculatif — je le fais quand même."
        )

    # spec 007 US3 (revu) : un plan actif existe → on montre un résumé et on attend
    # confirmation avant d'écraser quoi que ce soit. Venue du mode libre (aucun plan
    # actif) → comportement inchangé, exécution immédiate (rien à perdre).
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    if old_plan is None:
        await message.answer("⏳ Je régénère ton plan depuis ta forme actuelle…")
        await _regenerate(message, state, session, user, data["goal"], target_date)
        return

    await message.answer("⏳ Je prépare le nouveau plan pour confirmation…")
    new_plan, profile = await _build_new_plan(session, user, data["goal"], target_date)
    old_schema = TrainingPlanSchema.model_validate(old_plan.plan_technical)

    await state.update_data(
        _pending_goal=data["goal"],
        _pending_target_date=target_date.isoformat() if target_date else None,
        _pending_new_plan=new_plan.model_dump(mode="json"),
        _pending_profile=profile.model_dump(mode="json"),
    )
    await state.set_state(GoalStates.CONFIRM_REGEN)
    date_str = f" le {target_date:%d/%m/%Y}" if target_date else ""
    await message.answer(
        "🔎 <b>Nouveau plan proposé</b>\n\n"
        f"{new_plan.weeks_count} semaines (actuellement {old_schema.weeks_count}) — "
        f"objectif {data['goal']}{date_str}.\n\nConfirmer le changement ?",
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

    await callback.answer("Application en cours…")
    await callback.message.edit_text("⏳ J'applique le nouveau plan…")
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
    await callback.message.edit_text("Objectif inchangé — ton plan actuel reste actif.")
    await callback.answer()


async def _build_new_plan(session, user, goal: str, target_date: date | None):
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


async def _regenerate(message, state, session, user, goal: str, target_date: date | None) -> None:
    # spec 009 : old_plan is None when this is entered from freestyle mode (no plan to
    # diff against) — every use below is guarded accordingly. Kept as a thin wrapper
    # around _build_new_plan()/_apply_new_plan() so callers that need the whole thing
    # done in one shot (freestyle→goal, and every existing test) see no behavior change.
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    new_plan, profile = await _build_new_plan(session, user, goal, target_date)
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

    # Calendrier publié sous l'ancien plan → périmé (FR-014, réutilise spec 005). Rien à
    # vérifier si on vient du mode libre (aucun ancien plan, donc rien de publié).
    stale_note = ""
    if old_plan is not None:
        try:
            entries = await repo.publication_repo.get_active_entries_for_plan(
                session, user.id, old_plan.id
            )
            if entries:
                stale_note = (
                    f"\n⚠️ {len(entries)} séances publiées dans ton calendrier sous "
                    "l'ancien plan ne correspondent plus — relance /publish pour "
                    "re-synchroniser."
                )
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
                freestyle_withdrawn_note = (
                    f"\n📅 {withdrawn} séance(s) mode libre retirée(s) de ton calendrier."
                )
        except Exception:
            logger.warning("retrait des publications mode libre impossible après /goal")

    # Ce qui change / ce qui est gardé (FR-016). "Ce qui change" n'a de sens que s'il y
    # avait un ancien plan à comparer (spec 009 : entrée depuis mode libre = rien à diffé).
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    change_note = (
        f"\n\nCe qui change : périodisation refaite "
        f"({old_schema.weeks_count} → {new_plan.weeks_count} semaines), "
        f"départ depuis CTL {new_plan.initial_weekly_tss / 7:.0f}."
        if old_schema is not None else ""
    )
    summary = (
        f"✅ <b>Nouveau plan</b> — {new_plan.weeks_count} semaines"
        + (f" vers {goal} le {target_date:%d/%m/%Y}" if target_date else f" ({goal})")
        + change_note
        + f"\nCe qui est gardé : {len(logs)} séances loggées, ton historique d'adhérence, "
        f"tes réglages — rien n'a bougé."
        f"{stale_note}"
        f"{freestyle_withdrawn_note}"
    )
    await state.set_state(PlanStates.ACTIVE)
    await state.clear()
    await message.answer(summary, parse_mode="HTML")

