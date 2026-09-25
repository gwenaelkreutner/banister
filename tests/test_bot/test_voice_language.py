"""The voice chooser preserves selection while showing the configured language."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers import voice as voice_router
from app.core.localization import settings


@pytest.fixture
def voices(monkeypatch):
    entries = [
        SimpleNamespace(id="coach-default", name="Coach", voice="direct-expert"),
        SimpleNamespace(
            id="marseillais", name="Marseillais", voice="mec du Sud, franc, vivant, taquin",
        ),
        SimpleNamespace(
            id="pedagogue", name="Pédagogue", voice="clair, patient, explique le pourquoi",
        ),
    ]
    monkeypatch.setattr(voice_router, "available_voices", lambda: entries)
    monkeypatch.setattr(voice_router, "resolve_voice", lambda user: (entries[0], False))
    return entries


@pytest.mark.parametrize(
    ("language", "heading", "name", "style", "current", "setup", "unknown", "selected"),
    [
        ("en", "Coach voice", "Teacher", "explains the why", "(current)",
         "Please run /setup first.", "Unknown voice.", "Voice: Teacher ✓"),
        ("fr", "Voix du coach", "Pédagogue", "explique le pourquoi", "(actuelle)",
         "Fais d'abord /setup.", "Voix inconnue.", "Voix : Pédagogue ✓"),
    ],
)
async def test_voice_command_and_callbacks_use_app_language(
    monkeypatch, voices, language, heading, name, style, current, setup, unknown, selected,
):
    monkeypatch.setattr(settings, "app_language", language)
    set_voice = AsyncMock()
    monkeypatch.setattr(voice_router.repo.user_repo, "set_coach_voice", set_voice)
    message = SimpleNamespace(answer=AsyncMock())
    session, user = object(), object()

    await voice_router.cmd_voice(message, session, None)
    message.answer.assert_awaited_once_with(setup)

    message.answer.reset_mock()
    await voice_router.cmd_voice(message, session, user)
    body = message.answer.call_args.args[0]
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert heading in body and name in body and style in body and current in body
    assert markup.inline_keyboard[-1][0].text.endswith(name)
    assert markup.inline_keyboard[-1][0].callback_data == "voice:set:pedagogue"

    callback = SimpleNamespace(
        data="voice:set:missing", answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )
    await voice_router.voice_set(callback, session, user)
    callback.answer.assert_awaited_once_with(unknown, show_alert=True)
    set_voice.assert_not_awaited()

    callback.answer.reset_mock()
    callback.data = "voice:set:pedagogue"
    await voice_router.voice_set(callback, session, user)
    set_voice.assert_awaited_once_with(session, user, "pedagogue")
    callback.answer.assert_awaited_once_with(selected)
    assert current in callback.message.edit_text.call_args.args[0]
    assert callback.message.edit_text.call_args.kwargs["parse_mode"] == "HTML"
