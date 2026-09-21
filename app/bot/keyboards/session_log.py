from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.engine.rpe import RPE_EASY_VALUE, RPE_HARD_VALUE, RPE_NORMAL_VALUE


def rpe_emoji_keyboard(log_id: str) -> InlineKeyboardMarkup:
    """Clavier RPE pour une activité détectée par le poller (encode l'ID du log).

    3 boutons emoji pour l'expérience utilisateur (choix rapide d'une bande large) —
    mais chaque bouton écrit une valeur représentative sur l'échelle standard 1-10
    (app/engine/rpe.py), jamais une chaîne catégorielle : `session_logs.rpe` reste un
    seul type de donnée, que la valeur vienne de Telegram ou d'intervals.icu."""
    prefix = f"log:rpe:{log_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="😫 Dur",    callback_data=f"{prefix}:{RPE_HARD_VALUE}"),
            InlineKeyboardButton(text="😐 Normal", callback_data=f"{prefix}:{RPE_NORMAL_VALUE}"),
            InlineKeyboardButton(text="🙂 Facile", callback_data=f"{prefix}:{RPE_EASY_VALUE}"),
        ],
        [
            InlineKeyboardButton(text="Passer",    callback_data=f"{prefix}:skip"),
        ],
    ])
