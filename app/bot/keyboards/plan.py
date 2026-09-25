from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.localization import t


def week_navigation_keyboard(week_num: int, weeks_count: int) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    if week_num > 1:
        row.append(InlineKeyboardButton(
            text=t("plan.previous_week"),
            callback_data=f"plan:week:{week_num - 1}",
        ))
    if week_num < weeks_count:
        row.append(InlineKeyboardButton(
            text=t("plan.next_week"),
            callback_data=f"plan:week:{week_num + 1}",
        ))
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(
            text=t("plan.overview_button"),
            callback_data="plan:overview",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def overview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=t("plan.current_week_button"),
            callback_data="plan:current",
        ),
    ]])
