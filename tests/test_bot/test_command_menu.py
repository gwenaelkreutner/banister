"""Telegram's native command menu follows the installation language."""

from unittest.mock import AsyncMock

import pytest

from app.bot.setup import build_bot_commands, register_bot_commands
from app.config import settings


@pytest.mark.parametrize(
    ("language", "fitness_command", "summary_command", "first_description"),
    [
        ("en", "fitness", "summary", "Start / home"),
        ("fr", "forme", "recap", "Démarrer / accueil"),
    ],
)
def test_command_menu_names_descriptions_and_order(
    language, fitness_command, summary_command, first_description
):
    commands = build_bot_commands(language=language)

    assert [item.command for item in commands] == [
        "start", "setup", "plan", "week", fitness_command, summary_command,
        "review", "goal", "publish", "unpublish", "reminders", "voice",
        "reset", "cancel", "help",
    ]
    assert commands[0].description == first_description
    assert commands[4].description == (
        "Fitness metrics (CTL/ATL/TSB)"
        if language == "en" else "Métriques de forme (CTL/ATL/TSB)"
    )
    assert commands[5].description == (
        "Weekly summary" if language == "en" else "Récapitulatif hebdomadaire"
    )
    assert all(item.description for item in commands)


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.asyncio
async def test_register_bot_commands_uses_current_setting(monkeypatch, language):
    monkeypatch.setattr(settings, "app_language", language)
    bot = AsyncMock()

    await register_bot_commands(bot)

    bot.set_my_commands.assert_awaited_once_with(build_bot_commands(language=language))
