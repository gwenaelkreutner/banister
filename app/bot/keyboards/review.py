"""Claviers inline pour `/review` (picker séance + picker profondeur).

Préfixe callback : `review:` (alongside setup:/plan:/log:/chat:/rem:/pub:/goal:/voice:).
Deux claviers, deux étapes :
  - `review:pick:<log_id hex>:<depth|none>`  — choix de la séance parmi les 5 dernières
  - `review:depth:<log_id hex>:<depth>`      — choix de la profondeur (si pas donnée en CLI)

Pas de FSM ici (volontaire, symétrique du callback `freestyle:publish:<id>` de spec 010) :
la seule donnée transportée est un UUID + un flag de profondeur, qui tient largement dans
les 64 octets du `callback_data` Telegram — inutile de payer le coût d'un état FSM (perdu
au redémarrage du bot avec `MemoryStorage`) pour ça.
"""
from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models.session_log import SessionLog

# Labels pour l'affichage des boutons uniquement — distinct de WORKOUT_FR (narrator.py),
# qui couvre le workout_type *planifié* (4 valeurs). session_type_real (réalisé, mapper.py)
# en a 7 : long_ride/endurance/tempo/intervals/recovery/race/unknown.
_SESSION_TYPE_LABELS: dict[str, str] = {
    "long_ride": "Sortie longue",
    "endurance": "Endurance",
    "tempo": "Tempo",
    "intervals": "Intervalles",
    "recovery": "Récupération",
    "race": "Course",
}

DEPTH_LABELS: dict[str, str] = {"brief": "Bref", "default": "Standard", "deep": "Profond"}


def _session_label(log: SessionLog) -> str:
    type_label = _SESSION_TYPE_LABELS.get(log.session_type_real or "", "Séance")
    parts = [f"{log.logged_date:%d/%m}", type_label]
    if log.tss_actual is not None:
        parts.append(f"{log.tss_actual:.0f} TSS")
    return " · ".join(parts)


def recent_sessions_keyboard(
    logs: list[SessionLog], cli_depth: str | None
) -> InlineKeyboardMarkup:
    depth_token = cli_depth if cli_depth in ("brief", "deep") else "none"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_session_label(log),
                    callback_data=f"review:pick:{log.id.hex}:{depth_token}",
                )
            ]
            for log in logs
        ]
    )


def depth_keyboard(log_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label, callback_data=f"review:depth:{log_id.hex}:{depth}"
                )
                for depth, label in DEPTH_LABELS.items()
            ]
        ]
    )
