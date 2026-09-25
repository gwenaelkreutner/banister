"""Plan navigation labels are translated without changing callback routes."""

import pytest

from app.bot.keyboards.plan import overview_keyboard, week_navigation_keyboard
from app.config import settings


@pytest.mark.parametrize(
    ("language", "previous", "next_week", "overview", "current"),
    [
        ("en", "◀️ Previous week", "Next week ▶️", "📋 Overview", "📅 View current week"),
        ("fr", "◀️ Sem. précédente", "Sem. suivante ▶️", "📋 Vue d'ensemble",
         "📅 Voir semaine en cours"),
    ],
)
def test_plan_keyboard_labels_and_callbacks(
    monkeypatch, language, previous, next_week, overview, current
):
    monkeypatch.setattr(settings, "app_language", language)

    navigation = week_navigation_keyboard(2, 3).inline_keyboard
    assert [(button.text, button.callback_data) for button in navigation[0]] == [
        (previous, "plan:week:1"),
        (next_week, "plan:week:3"),
    ]
    assert [(button.text, button.callback_data) for button in navigation[1]] == [
        (overview, "plan:overview"),
    ]
    overview_buttons = overview_keyboard().inline_keyboard[0]
    assert [(button.text, button.callback_data) for button in overview_buttons] == [
        (current, "plan:current"),
    ]


def test_first_and_last_week_only_offer_valid_navigation():
    first = week_navigation_keyboard(1, 3).inline_keyboard
    last = week_navigation_keyboard(3, 3).inline_keyboard

    assert [button.callback_data for button in first[0]] == ["plan:week:2"]
    assert [button.callback_data for button in last[0]] == ["plan:week:2"]
