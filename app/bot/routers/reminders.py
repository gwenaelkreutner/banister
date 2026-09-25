"""
Router : /reminders — gestion des rappels de séance matinaux.

Menu inline état-dépendant :
  - Si activé  : bouton "Désactiver" + grille d'heures (heure active cochée)
  - Si désactivé : bouton "Activer" uniquement
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db.models.user import User

logger = logging.getLogger(__name__)
router = Router()

# Créneaux disponibles (heure, minute) — heure de Paris (Europe/Paris, CET/CEST selon
# la saison ; voir PARIS_TZ dans app/main.py)
_TIME_SLOTS: list[tuple[int, int]] = [
    (6, 0),
    (7, 0),
    (7, 30),
    (8, 0),
    (9, 0),
]


def _time_label(h: int, m: int) -> str:
    if m:
        return t("reminders.time_label_hour_minute", hour=h, minute=f"{m:02d}")
    return t("reminders.time_label_hour", hour=h)


def _build_keyboard(user: User) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if user.reminders_enabled:
        rows.append([
            InlineKeyboardButton(text=t("reminders.disable_button"), callback_data="rem:toggle")
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
            InlineKeyboardButton(text=t("reminders.enable_button"), callback_data="rem:toggle")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_text(user: User) -> str:
    if user.reminders_enabled:
        status = t("reminders.status_enabled")
        label = _time_label(user.reminder_hour, user.reminder_minute)
        heure = t("reminders.time_line", time=label)
    else:
        status = t("reminders.status_disabled")
        heure = ""
    return t("reminders.menu", status=status, time_line=heure)


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, user: User):
    if not user.onboarding_completed:
        await message.answer(t("reminders.onboarding_required"))
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
        t("reminders.enabled_confirmation")
        if user.reminders_enabled
        else t("reminders.disabled_confirmation")
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

    await callback.answer(t("reminders.time_confirmation", time=_time_label(h, m)))
    await callback.message.edit_text(
        _build_text(user),
        reply_markup=_build_keyboard(user),
        parse_mode="HTML",
    )
