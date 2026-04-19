"""
Router : /reminders — gestion des rappels de séance matinaux.

Menu inline état-dépendant :
  - Si activé  : bouton "Désactiver" + grille d'heures (heure active cochée)
  - Si désactivé : bouton "Activer" uniquement
"""

import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User

logger = logging.getLogger(__name__)
router = Router()

# Créneaux disponibles (heure, minute) — UTC+1 (CET)
_TIME_SLOTS: list[tuple[int, int]] = [
    (6, 0),
    (7, 0),
    (7, 30),
    (8, 0),
    (9, 0),
]


def _time_label(h: int, m: int) -> str:
    return f"{h}h{m:02d}" if m else f"{h}h"


def _build_keyboard(user: User) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if user.reminders_enabled:
        rows.append([
            InlineKeyboardButton(text="❌ Désactiver les rappels", callback_data="rem:toggle")
        ])
        # Grille d'heures sur 2 colonnes
        hour_buttons = []
        for h, m in _TIME_SLOTS:
            active = (user.reminder_hour == h and user.reminder_minute == m)
            label = f"✓ {_time_label(h, m)}" if active else _time_label(h, m)
            hour_buttons.append(
                InlineKeyboardButton(text=label, callback_data=f"rem:time:{h}:{m}")
            )
        # 2 boutons par ligne, sauf le dernier si impair
        for i in range(0, len(hour_buttons), 2):
            rows.append(hour_buttons[i:i + 2])
    else:
        rows.append([
            InlineKeyboardButton(text="✅ Activer les rappels", callback_data="rem:toggle")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_text(user: User) -> str:
    if user.reminders_enabled:
        status = "✅ Activé"
        heure = f"\nHeure  : <b>{_time_label(user.reminder_hour, user.reminder_minute)}</b> (UTC+1)"
    else:
        status = "❌ Désactivé"
        heure = ""
    return f"🔔 <b>Rappels de séance</b>\n\nStatut : {status}{heure}"


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, user: User):
    if not user.onboarding_completed:
        await message.answer("Complète d'abord ton onboarding avec /start.")
        return

    await message.answer(
        _build_text(user),
        reply_markup=_build_keyboard(user),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "rem:toggle")
async def cb_toggle(callback: CallbackQuery, session: AsyncSession, user: User):
    user.reminders_enabled = not user.reminders_enabled
    await session.flush()

    await callback.answer(
        "Rappels activés ✅" if user.reminders_enabled else "Rappels désactivés ❌"
    )
    await callback.message.edit_text(
        _build_text(user),
        reply_markup=_build_keyboard(user),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("rem:time:"))
async def cb_set_time(callback: CallbackQuery, session: AsyncSession, user: User):
    _, _, h_str, m_str = callback.data.split(":")
    h, m = int(h_str), int(m_str)

    user.reminder_hour = h
    user.reminder_minute = m
    # Réinitialise la date d'envoi pour forcer un re-check aujourd'hui si l'heure est passée
    user.reminder_last_sent_at = None
    await session.flush()

    await callback.answer(f"Rappel réglé à {_time_label(h, m)} ✓")
    await callback.message.edit_text(
        _build_text(user),
        reply_markup=_build_keyboard(user),
        parse_mode="HTML",
    )
