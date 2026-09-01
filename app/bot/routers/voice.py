"""Router : /voice — spec 007 US5.

Choisir la voix du coach : liste les personas/*.yaml, écrit users.coach_voice,
effet au message suivant (FR-021..FR-026).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repositories as repo
from app.services.coach_voice import available_voices

router = Router(name="voice")


def _voice_menu(current: str) -> InlineKeyboardMarkup:
    rows = []
    for v in available_voices():
        mark = "▸ " if v.id == current else "  "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{v.name}", callback_data=f"voice:set:{v.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _voice_text(current: str) -> str:
    lines = ["🗣️ <b>Voix du coach</b>", ""]
    for v in available_voices():
        tag = "  <i>(actuelle)</i>" if v.id == current else ""
        lines.append(f"  <b>{v.name}</b> — {v.voice}{tag}")
    lines += ["", "Elle prend effet dès ton prochain message."]
    return "\n".join(lines)


@router.message(Command("voice"))
async def cmd_voice(message: Message, session: AsyncSession, user) -> None:
    if user is None:
        await message.answer("Fais d'abord /setup.")
        return
    current = user.coach_voice or settings.persona
    await message.answer(
        _voice_text(current), parse_mode="HTML", reply_markup=_voice_menu(current)
    )


@router.callback_query(F.data.startswith("voice:set:"))
async def voice_set(callback: CallbackQuery, session: AsyncSession, user) -> None:
    voice_id = callback.data.split(":", 2)[2]
    known = {v.id for v in available_voices()}
    if voice_id not in known:
        await callback.answer("Voix inconnue.", show_alert=True)
        return
    await repo.user_repo.set_coach_voice(session, user, voice_id)
    name = next(v.name for v in available_voices() if v.id == voice_id)
    await callback.answer(f"Voix : {name} ✓")
    await callback.message.edit_text(
        _voice_text(voice_id), parse_mode="HTML", reply_markup=_voice_menu(voice_id)
    )
