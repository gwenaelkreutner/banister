"""Clavier inline pour `/review` (picker de séance).

Préfixe callback : `review:` (alongside setup:/plan:/log:/chat:/rem:/pub:/goal:/voice:).
Une seule étape : `review:pick:<log_id hex>` — choix de la séance parmi les 5 dernières.
Un ancien picker de profondeur (brief/default/deep) a été retiré (2026-09-21, décision
owner) : les 3 modes ne se différenciaient que par deux consignes molles jamais
appliquées par force, donc convergeaient en pratique — un seul mode bien calibré vaut
mieux (voir `app/llm/prompts.py::build_review_system_prompt`).

Pas de FSM ici (volontaire, symétrique du callback `freestyle:publish:<id>` de spec 010) :
la seule donnée transportée est un UUID, qui tient largement dans les 64 octets du
`callback_data` Telegram — inutile de payer le coût d'un état FSM (perdu au redémarrage
du bot avec `MemoryStorage`) pour ça.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.localization import t
from app.db.models.session_log import SessionLog

# Labels pour l'affichage des boutons uniquement — distinct de WORKOUT_FR (narrator.py),
# qui couvre le workout_type *planifié* (4 valeurs). session_type_real (réalisé, mapper.py)
# en a 7 : long_ride/endurance/tempo/intervals/recovery/race/unknown.
_SESSION_TYPE_KEYS: dict[str, str] = {
    "long_ride": "review.type_long_ride",
    "endurance": "review.type_endurance",
    "tempo": "review.type_tempo",
    "intervals": "review.type_intervals",
    "recovery": "review.type_recovery",
    "race": "review.type_race",
}


def _session_label(log: SessionLog) -> str:
    type_key = _SESSION_TYPE_KEYS.get(log.session_type_real or "", "review.type_unknown")
    tss = (
        t("review.session_tss", tss=f"{log.tss_actual:.0f}")
        if log.tss_actual is not None else ""
    )
    return t("review.session_label", date=log.logged_date.strftime("%d/%m"),
             type=t(type_key), tss=tss)


def recent_sessions_keyboard(logs: list[SessionLog]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_session_label(log),
                    callback_data=f"review:pick:{log.id.hex}",
                )
            ]
            for log in logs
        ]
    )
