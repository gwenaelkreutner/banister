from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def rpe_emoji_keyboard_manual(week: int, day: int, duration: int) -> InlineKeyboardMarkup:
    """Clavier RPE après saisie manuelle (encode week, day et duration dans le callback)."""
    prefix = f"log:rpe:{week}:{day}:{duration}"
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


def rpe_emoji_keyboard_strava(log_id: str) -> InlineKeyboardMarkup:
    """Clavier RPE pour une activité Strava détectée (encode l'ID du log)."""
    prefix = f"log:strava_rpe:{log_id}"
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


def session_action_keyboard(week: int, day: int) -> InlineKeyboardMarkup:
    """Boutons 'Faite' / 'Sautée' pour le mode manuel."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Séance faite",  callback_data=f"log:done:{week}:{day}"),
        InlineKeyboardButton(text="⏭️ Sautée",        callback_data=f"log:skip:{week}:{day}"),
    ]])


def duration_keyboard(planned_minutes: int) -> InlineKeyboardMarkup:
    """Boutons de durée réelle autour de la durée planifiée."""
    shorter = max(15, planned_minutes - 15)
    longer = planned_minutes + 15
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"{shorter} min",         callback_data=f"log:dur:{shorter}"),
            InlineKeyboardButton(text=f"{planned_minutes} min ✓", callback_data=f"log:dur:{planned_minutes}"),
            InlineKeyboardButton(text=f"{longer} min",          callback_data=f"log:dur:{longer}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Autre durée",         callback_data="log:dur:custom"),
        ],
    ])
