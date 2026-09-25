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
from app.core.localization import t
from app.db import repositories as repo
from app.db.models.chat_message import ChatMessage
from app.db.models.session_log import SessionLog
from app.db.models.weekly_adherence import WeeklyAdherence

router = Router(name="reset")

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
        await message.answer(t("reset.no_account"))
        return

    c = await _counts(session, user.id)
    if not any((c["sessions"], c["messages"], c["adherence"], c["has_plan"], c["has_profile"])):
        await message.answer(t("reset.no_data"))
        return

    lines = [t("reset.warning"), ""]
    if c["sessions"]:
        lines.append(t("reset.session_count", count=c["sessions"]))
    if c["messages"]:
        lines.append(t("reset.message_count", count=c["messages"]))
    if c["adherence"]:
        lines.append(t("reset.adherence_count", count=c["adherence"]))
    if c["has_plan"]:
        lines.append(t("reset.active_plan"))
    if c["has_profile"]:
        lines.append(t("reset.profile"))
    lines += [
        "",
        t("reset.retained_data"),
        "",
        t("reset.confirm_instruction", word=t("reset.confirm_word")),
    ]
    await state.clear()
    await state.set_state(ResetStates.CONFIRM)
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(ResetStates.CONFIRM, F.text)
async def reset_confirm(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    if message.text.strip() != t("reset.confirm_word"):
        await state.clear()
        await message.answer(t("reset.cancelled"))
        return

    counts = await repo.user_repo.purge_athlete_data(session, user.id)
    user.onboarding_completed_at = None  # le prochain /setup est un vrai premier run
    await session.flush()
    await state.clear()

    total = sum(counts.values())
    await message.answer(t("reset.completed", count=total))
