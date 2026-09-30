"""Telegram shows actual application tool calls before the final answer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers.chat import _ToolTrace
from app.llm.chat_client import run_agentic_loop


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


@pytest.mark.asyncio
async def test_respond_without_tool_never_creates_or_changes_trace():
    incoming = _IncomingMessage()
    trace = _ToolTrace(incoming)

    await trace("respond_without_tool", "started")
    await trace("respond_without_tool", "finished")
    assert incoming.sent == []

    await trace("get_fitness_history", "started")
    await trace("respond_without_tool", "started")
    await trace("respond_without_tool", "finished")
    await trace("get_fitness_history", "finished")

    assert incoming.sent[0].versions == [
        "Outils utilisés :\n⏳ get_fitness_history",
        "Outils utilisés :\n✅ get_fitness_history",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_hidden_no_action_reply_finishes_after_one_llm_call(monkeypatch, native):
    incoming = _IncomingMessage()
    function = SimpleNamespace(name="respond_without_tool", arguments='{"answer":"Bonjour !"}')
    response = SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(
            finish_reason="tool_calls" if native else "stop",
            message=SimpleNamespace(
                tool_calls=[SimpleNamespace(id="call-1", function=function)] if native else None,
                content=None if native else (
                    'TOOLCALL>[{"name":"respond_without_tool",'
                    '"arguments":{"answer":"Bonjour !"}}]>'
                ),
            ),
        )],
    )
    create = AsyncMock(return_value=response)
    execute = AsyncMock()
    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    answer, used, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "Salut"}], tools=[],
        tool_executor=execute, on_tool_event=_ToolTrace(incoming),
    )

    assert (answer, used, log) == ("Bonjour !", None, [])
    create.assert_awaited_once()
    execute.assert_not_awaited()
    assert incoming.sent == []
