from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def week_navigation_keyboard(week_num: int, weeks_count: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    if week_num > 1:
        row.append(InlineKeyboardButton(text="◀️ Sem. précédente", callback_data=f"plan:week:{week_num - 1}"))
    if week_num < weeks_count:
        row.append(InlineKeyboardButton(text="Sem. suivante ▶️", callback_data=f"plan:week:{week_num + 1}"))
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="📋 Vue d'ensemble", callback_data="plan:overview"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def overview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📅 Voir semaine en cours", callback_data="plan:current"),
    ]])
