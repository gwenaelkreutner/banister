"""The model must explicitly select a real tool or a no-action reply."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.config import settings
from app.db.models.user import User
from app.llm.chat import _verify_action_claim
from app.llm.chat_client import _TOOL_DECISION_RULE, run_agentic_loop


def _tool_call(name, arguments):
    return SimpleNamespace(id="call-1", function=SimpleNamespace(name=name, arguments=arguments))


def _response(tool_calls=None, content=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    reason = "tool_calls" if tool_calls else "stop"
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=reason, message=message)], usage=None,
    )


def test_no_tool_cannot_claim_a_write():
    assert _verify_action_claim("C'est noté, c'est enregistré.", []) == (
        "Aucun outil n'a été exécuté : rien n'a été enregistré ni modifié."
    )
    assert _verify_action_claim("Voici une estimation.", []) == "Voici une estimation."


@pytest.mark.asyncio
async def test_no_action_decision_does_not_execute_a_tool(monkeypatch):
    calls = []
    events = []

    async def create(**kwargs):
        calls.append(kwargs)
        return _response([_tool_call("respond_without_tool", '{"answer":"Je peux t’aider."}')])

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        raise AssertionError("No application tool should run")

    async def on_event(name, status):
        events.append((name, status))

    answer, used, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "Bonjour"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory"}}],
        tool_executor=execute, on_tool_event=on_event,
    )
    assert len(calls) == 1
    assert calls[0]["tool_choice"] == "required"
    assert calls[0]["messages"][0]["content"] == f"test\n\n{_TOOL_DECISION_RULE}"
    assert {tool["function"]["name"] for tool in calls[0]["tools"]} == {
        "respond_without_tool", "update_coach_memory",
    }
    assert (answer, used, log) == ("Je peux t’aider.", None, [])
    assert events == [("respond_without_tool", "started"), ("respond_without_tool", "finished")]


@pytest.mark.asyncio
async def test_required_plain_text_is_retried_before_any_tool_runs(monkeypatch):
    calls = []
    executed = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _response(content="Banane notée.")
        if len(calls) == 2:
            return _response([_tool_call(
                "update_coach_memory", '{"action":"add","amount":100}',
            )])
        return _response(content="C'est noté.")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        executed.append((name, args))
        return {"ok": True}

    answer, _, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "J'ai mangé une banane"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory", "parameters": {
            "type": "object", "properties": {
                "action": {"type": "string", "enum": ["add", "remove"]},
                "amount": {"type": "integer", "minimum": 1},
            }, "required": ["action", "amount"],
        }}}], tool_executor=execute,
    )

    assert [call["tool_choice"] for call in calls] == ["required", "required", "auto"]
    assert [call["messages"][0]["content"] for call in calls] == [
        f"test\n\n{_TOOL_DECISION_RULE}", f"test\n\n{_TOOL_DECISION_RULE}", "test",
    ]
    assert [call["model"] for call in calls[:2]] == [
        settings.chat_model, "google/gemini-3-flash-preview",
    ]
    assert calls[1]["max_tokens"] == 1024
    assert executed == [("update_coach_memory", {"action": "add", "amount": 100})]
    assert len(log) == 1
    assert answer == "C'est noté."


@pytest.mark.asyncio
async def test_repeated_required_plain_text_never_claims_a_write(monkeypatch):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return _response(content="Banane notée.")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        raise AssertionError("No application tool should run")

    answer, used, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "J'ai mangé une banane"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory"}}],
        tool_executor=execute,
    )

    assert len(calls) == 2
    assert [call["tool_choice"] for call in calls] == ["required", "required"]
    assert calls[1]["model"] == "google/gemini-3-flash-preview"
    assert (answer, used, log) == (
        "Aucun outil n'a été exécuté : rien n'a été enregistré ni modifié.", None, [],
    )


@pytest.mark.asyncio
async def test_fallback_model_error_returns_explicit_no_action(monkeypatch):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _response(content="Je l'ai ajouté.")
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        raise AssertionError("No application tool should run")

    answer, used, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "Ajoute une banane"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory"}}],
        tool_executor=execute,
    )

    assert len(calls) == 2
    assert used is None and log == []
    assert answer == "Aucun outil n'a été exécuté : rien n'a été enregistré ni modifié."


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("update_coach_memory", '{"action":"add"}'),
    ("update_coach_memory", '{"action":"add","amount":0}'),
    ("update_coach_memory", '{"action":"add","amount":"100"}'),
    ("unknown_tool", '{}'),
    ("update_coach_memory", '{broken'),
])
async def test_invalid_tool_calls_never_reach_executor(monkeypatch, name, arguments):
    calls = []
    events = []
    executed = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _response([_tool_call(name, arguments)])
        return _response(content="C'est enregistré.")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        executed.append((name, args))
        raise AssertionError("Invalid tool must not execute")

    async def on_event(name, status):
        events.append((name, status))

    answer, _, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "Ajoute un repas"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory", "parameters": {
            "type": "object", "properties": {
                "action": {"type": "string", "enum": ["add", "remove"]},
                "amount": {"type": "integer", "minimum": 1},
            }, "required": ["action", "amount"],
        }}}], tool_executor=execute, on_tool_event=on_event,
    )

    assert answer == "C'est enregistré."
    assert executed == []
    assert log[0]["result"].get("error")
    assert events == [(name, "started"), (name, "failed")]


def test_action_claim_after_unrelated_or_failed_tool_is_not_trusted():
    assert _verify_action_claim("Banane notée.", [
        {"name": "get_nutrition_summary", "result": {"days": []}},
    ]) == "Aucune modification n'a été effectuée pour ce message."


@pytest.mark.asyncio
async def test_same_mutation_repeated_after_tool_result_runs_once(monkeypatch):
    calls = []
    executed = []
    write = _tool_call("update_coach_memory", '{"action":"add","amount":100}')

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 2:
            return _response([write])
        return _response(content="Terminé.")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        executed.append((name, args))
        return {"ok": True}

    _, _, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "Une banane"}],
        tools=[{"type": "function", "function": {"name": "update_coach_memory"}}],
        tool_executor=execute,
    )

    assert len(calls) == 3
    assert len(executed) == 1
    assert len(log) == 1
    assert _verify_action_claim("Je note ça.", [
        {"name": "update_coach_memory", "result": {"ok": False, "error": "failed"}},
    ]) == "Aucune modification n'a été effectuée pour ce message."
