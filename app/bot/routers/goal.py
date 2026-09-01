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
from app.db import repositories as repo
from app.engine.plan_builder import generate_plan
from app.engine.schemas import TrainingPlanSchema
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)
router = Router(name="goal")


def _goal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Événement cible", callback_data="goal:type:event")],
        [InlineKeyboardButton(text="💚 Forme générale", callback_data="goal:type:fitness")],
        [InlineKeyboardButton(text="⚡ Performance", callback_data="goal:type:performance")],
        [InlineKeyboardButton(text="🔹 Autre", callback_data="goal:type:other")],
    ])


@router.message(Command("goal"))
async def cmd_goal(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    if user is None or not user.onboarding_completed:
        await message.answer("Fais d'abord /setup.")
        return
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    if plan is None or profile_row is None:
        await message.answer("Aucun plan actif — lance /setup.")
        return

    await state.clear()
    await state.update_data(_old_plan_id=str(plan.id))
    await state.set_state(GoalStates.GOAL)
    await message.answer(
        "On change d'objectif. Je garde tout le reste — tes séances faites, ton "
        "historique, notre conversation.\n\nNouvel objectif ?",
        reply_markup=_goal_kb(),
    )


@router.callback_query(GoalStates.GOAL, F.data.startswith("goal:type:"))
async def goal_type(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(goal=callback.data.split(":")[2])
    await state.set_state(GoalStates.DATE)
    await callback.message.edit_text(
        "📅 Date de l'objectif ? Format <code>AAAA-MM-JJ</code>, ou <code>aucune</code>.",
        parse_mode="HTML",
    )
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

    await message.answer("⏳ Je régénère ton plan depuis ta forme actuelle…")
    await _regenerate(message, state, session, user, data["goal"], target_date)


async def _regenerate(message, state, session, user, goal: str, target_date: date | None) -> None:
    old_plan = await repo.plan_repo.get_active_plan(session, user.id)
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    old_schema = TrainingPlanSchema.model_validate(old_plan.plan_technical)

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
    from app.engine.schemas import AthleteProfileSchema

    profile = AthleteProfileSchema.model_validate(pdata)
    new_plan = generate_plan(profile)

    await repo.plan_repo.deactivate_all_for_user(session, user.id)
    await repo.plan_repo.create(
        session=session,
        user_id=user.id,
        plan_technical=new_plan.model_dump(mode="json"),
        start_date=new_plan.start_date or date.today(),
        end_date=new_plan.end_date or date.today(),
    )
    await repo.profile_repo.update(session, profile_row, profile.model_dump(mode="json"))

    # Calendrier publié sous l'ancien plan → périmé (FR-014, réutilise spec 005).
    stale_note = ""
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

    # Ce qui change / ce qui est gardé (FR-016).
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    summary = (
        f"✅ <b>Nouveau plan</b> — {new_plan.weeks_count} semaines"
        + (f" vers {goal} le {target_date:%d/%m/%Y}" if target_date else f" ({goal})")
        + f"\n\nCe qui change : périodisation refaite "
        f"({old_schema.weeks_count} → {new_plan.weeks_count} semaines), "
        f"départ depuis CTL {new_plan.initial_weekly_tss / 7:.0f}."
        f"\nCe qui est gardé : {len(logs)} séances loggées, ton historique d'adhérence, "
        f"tes réglages — rien n'a bougé."
        f"{stale_note}"
    )
    await state.set_state(PlanStates.ACTIVE)
    await state.clear()
    await message.answer(summary, parse_mode="HTML")

