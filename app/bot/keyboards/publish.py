"""Approve / decline inline keyboard for calendar publication (spec 005 US1 = T017).

Callback prefix: `pub:` (alongside setup:/plan:/log:/chat:/rem:). Two actions only —
`pub:approve:<approval_id>` and `pub:decline:<approval_id>`; the approval id is carried
in the callback so the write is bound to exactly the request that was shown (FR-004).
"""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.localization import t


def approval_keyboard(approval_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("publish.approve_button"), callback_data=f"pub:approve:{approval_id}"
                ),
                InlineKeyboardButton(
                    text=t("publish.cancel_button"), callback_data=f"pub:decline:{approval_id}"
                ),
            ]
        ]
    )
