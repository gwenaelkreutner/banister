"""Language selection for common Telegram commands."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers.common import cmd_cancel, cmd_help, cmd_start
from app.bot.states import PlanStates
from app.core.localization import settings


@pytest.fixture
def message():
    return SimpleNamespace(answer=AsyncMock())


@pytest.fixture
def state():
    return SimpleNamespace(set_state=AsyncMock(), get_state=AsyncMock(), clear=AsyncMock())


@pytest.mark.parametrize(
    ("language", "welcome", "returning", "none", "cancelled", "help_title", "help_tail"),
    [
        ("en", "Welcome to", "Welcome back, Alex!", "Nothing to cancel.",
         "❌ Operation cancelled.", "Banister — Help", "without a special command."),
        ("fr", "Bienvenue sur", "Bon retour, Alex !", "Rien à annuler.",
         "❌ Opération annulée.", "Banister — Aide", "sans commande particulière."),
    ],
)
async def test_common_commands_follow_app_language(
    monkeypatch, message, state, language, welcome, returning, none, cancelled,
    help_title, help_tail,
):
    monkeypatch.setattr(settings, "app_language", language)

    await cmd_start(message, state, None)
    assert welcome in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs == {"parse_mode": "HTML"}
    state.set_state.assert_not_awaited()

    message.answer.reset_mock()
    await cmd_start(message, state, SimpleNamespace(first_name="Alex"))
    assert returning in message.answer.call_args.args[0]
    state.set_state.assert_awaited_once_with(PlanStates.ACTIVE)

    message.answer.reset_mock()
    state.get_state.return_value = None
    await cmd_cancel(message, state)
    message.answer.assert_awaited_once_with(none)
    state.clear.assert_not_awaited()

    message.answer.reset_mock()
    state.get_state.return_value = "setup"
    await cmd_cancel(message, state)
    message.answer.assert_awaited_once_with(cancelled)
    state.clear.assert_awaited_once()

    message.answer.reset_mock()
    await cmd_help(message)
    help_text = message.answer.call_args.args[0]
    assert help_title in help_text
    assert help_text.endswith(help_tail)
    assert all(command in help_text for command in ("/setup", "/plan", "/publish", "/cancel"))
    if language == "en":
        assert all(command in help_text for command in ("/fitness", "/summary", "/review"))
        assert "/forme" not in help_text and "/recap" not in help_text
    else:
        assert "/forme" in help_text and "/recap" in help_text
    assert message.answer.call_args.kwargs == {"parse_mode": "HTML"}
