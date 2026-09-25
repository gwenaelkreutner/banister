"""Router : /voice — spec 007 US5.

Choisir la voix du coach : liste les personas/*.yaml, écrit users.coach_voice,
effet au message suivant (FR-021..FR-026).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db import repositories as repo
from app.services.coach_voice import available_voices, resolve_voice

router = Router(name="voice")

def _display_name(voice) -> str:
    if voice.id in {"marseillais", "pedagogue"}:
        return t(f"voice.{voice.id}.name")
    return voice.name


def _display_style(voice) -> str:
    if voice.id in {"marseillais", "pedagogue"}:
        return t(f"voice.{voice.id}.style")
    return voice.voice


def _voice_menu(current: str) -> InlineKeyboardMarkup:
    rows = []
    for v in available_voices():
        mark = "▸ " if v.id == current else "  "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{_display_name(v)}", callback_data=f"voice:set:{v.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _voice_text(current: str) -> str:
    lines = [t("voice.title"), ""]
    for v in available_voices():
        tag = t("voice.current") if v.id == current else ""
        lines.append(f"  <b>{_display_name(v)}</b> — {_display_style(v)}{tag}")
    lines += ["", t("voice.takes_effect")]
    return "\n".join(lines)


@router.message(Command("voice"))
async def cmd_voice(message: Message, session: AsyncSession, user) -> None:
    if user is None:
        await message.answer(t("voice.setup_first"))
        return
    current = resolve_voice(user)[0].id
    await message.answer(
        _voice_text(current), parse_mode="HTML", reply_markup=_voice_menu(current)
    )


@router.callback_query(F.data.startswith("voice:set:"))
async def voice_set(callback: CallbackQuery, session: AsyncSession, user) -> None:
    voice_id = callback.data.split(":", 2)[2]
    known = {v.id for v in available_voices()}
    if voice_id not in known:
        await callback.answer(t("voice.unknown"), show_alert=True)
        return
    await repo.user_repo.set_coach_voice(session, user, voice_id)
    voice = next(v for v in available_voices() if v.id == voice_id)
    await callback.answer(t("voice.selected", name=_display_name(voice)))
    await callback.message.edit_text(
        _voice_text(voice_id), parse_mode="HTML", reply_markup=_voice_menu(voice_id)
    )
