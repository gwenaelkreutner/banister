from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.localization import t
from app.engine.rpe import rpe_scale_labels


def rpe_scale_keyboard(log_id: str) -> InlineKeyboardMarkup:
    """Choix précis 1-10 pour l'activité détectée par le poller."""
    prefix = f"log:rpe:{log_id}"
    rows = [
        [InlineKeyboardButton(text=f"{value} · {label}", callback_data=f"{prefix}:{value}")]
        for value, label in enumerate(rpe_scale_labels(), start=1)
    ]
    rows.append([
        InlineKeyboardButton(text=t("session_log.rpe_skip_button"), callback_data=f"{prefix}:skip")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
