"""Telegram shows actual application tool calls before the final answer."""
import pytest

from app.bot.routers.chat import _ToolTrace


class _SentMessage:
    def __init__(self, text):
        self.versions = [text]

    async def edit_text(self, text):
        self.versions.append(text)


class _IncomingMessage:
    def __init__(self):
        self.sent = []

    async def answer(self, text):
        message = _SentMessage(text)
        self.sent.append(message)
        return message


@pytest.mark.asyncio
async def test_one_message_updates_for_every_tool_call_and_status():
    incoming = _IncomingMessage()
    trace = _ToolTrace(incoming)

    await trace("get_fitness_history", "started")
    assert len(incoming.sent) == 1
    assert incoming.sent[0].versions[0] == "Outils utilisés :\n⏳ get_fitness_history"

    await trace("log_meal", "started")
    await trace("get_fitness_history", "finished")
    await trace("log_meal", "failed")

    assert len(incoming.sent) == 1
    assert incoming.sent[0].versions[-1] == (
        "Outils utilisés :\n✅ get_fitness_history\n❌ log_meal"
    )


@pytest.mark.asyncio
async def test_repeated_same_tool_keeps_separate_status_lines():
    incoming = _IncomingMessage()
    trace = _ToolTrace(incoming)

    await trace("log_meal", "started")
    await trace("log_meal", "finished")
    await trace("log_meal", "started")
    await trace("log_meal", "failed")

    assert incoming.sent[0].versions[-1] == (
        "Outils utilisés :\n✅ log_meal\n❌ log_meal"
    )
