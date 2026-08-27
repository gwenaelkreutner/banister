from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def rpe_emoji_keyboard(log_id: str) -> InlineKeyboardMarkup:
    """Clavier RPE pour une activité détectée par le poller (encode l'ID du log)."""
    prefix = f"log:rpe:{log_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="😫 Dur",    callback_data=f"{prefix}:hard"),
            InlineKeyboardButton(text="😐 Normal", callback_data=f"{prefix}:normal"),
            InlineKeyboardButton(text="🙂 Facile", callback_data=f"{prefix}:easy"),
        ],
        [
            InlineKeyboardButton(text="Passer",    callback_data=f"{prefix}:skip"),
        ],
    ])
