"""Router : /reset — spec 007 US4.

Recommencer de zéro — action distincte de /goal : liste ce qui sera supprimé,
confirmation tapée, suppressions locales uniquement, ne touche jamais intervals.icu
(FR-017..FR-020, SC-006, SC-007).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import ResetStates
from app.db import repositories as repo
from app.db.models.chat_message import ChatMessage
from app.db.models.session_log import SessionLog
from app.db.models.weekly_adherence import WeeklyAdherence

router = Router(name="reset")

_CONFIRM_WORD = "SUPPRIMER"


async def _counts(session: AsyncSession, user_id) -> dict[str, int]:
    async def n(model) -> int:
        return await session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        ) or 0

    plan = await repo.plan_repo.get_active_plan(session, user_id)
    profile = await repo.profile_repo.get_by_user_id(session, user_id)
    return {
        "sessions": await n(SessionLog),
        "messages": await n(ChatMessage),
        "adherence": await n(WeeklyAdherence),
        "has_plan": 1 if plan is not None else 0,
        "has_profile": 1 if profile is not None else 0,
    }


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    if user is None:
        await message.answer("Rien à réinitialiser — fais /setup.")
        return

    c = await _counts(session, user.id)
    if not any((c["sessions"], c["messages"], c["adherence"], c["has_plan"], c["has_profile"])):
        await message.answer("Tu n'as pas encore de données à effacer.")
        return

    lines = ["⚠️ <b>Recommencer de zéro</b>. Voici ce qui sera SUPPRIMÉ définitivement :", ""]
    if c["sessions"]:
        lines.append(f"  • {c['sessions']} séances enregistrées")
    if c["messages"]:
        lines.append(f"  • {c['messages']} messages de conversation")
    if c["adherence"]:
        lines.append(f"  • ton historique d'adhérence ({c['adherence']} semaines)")
    if c["has_plan"]:
        lines.append("  • ton plan actif")
    if c["has_profile"]:
        lines.append("  • ton profil")
    lines += [
        "",
        "Ce qui n'est <b>PAS</b> touché : ton compte intervals.icu, tes activités là-bas, "
        "ta voix de coach.",
        "",
        f"Pour confirmer, écris exactement : <code>{_CONFIRM_WORD}</code>",
    ]
    await state.clear()
    await state.set_state(ResetStates.CONFIRM)
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(ResetStates.CONFIRM, F.text)
async def reset_confirm(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    if message.text.strip() != _CONFIRM_WORD:
        await state.clear()
        await message.answer("Rien n'a été supprimé.")
        return

    counts = await repo.user_repo.purge_athlete_data(session, user.id)
    user.onboarding_completed_at = None  # le prochain /setup est un vrai premier run
    await session.flush()
    await state.clear()

    total = sum(v for k, v in counts.items())
    await message.answer(
        f"✅ C'est fait — {total} enregistrements supprimés. Ton compte intervals.icu "
        "est intact. Lance /setup quand tu veux repartir.",
    )
