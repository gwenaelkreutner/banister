"""The model must explicitly select a real tool or a no-action reply."""
from datetime import date
from types import SimpleNamespace

import pytest

from app.db.models.user import User
from app.db.repositories import meal_entry_repo
from app.llm.chat import _tool_log_meal, _verify_action_claim
from app.llm.chat_client import run_agentic_loop


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
    assert _verify_action_claim("C'est noté.", [
        {"name": "log_meal", "result": {"ok": False}},
    ]) == "Le repas n'a pas été enregistré. Le détail est juste en dessous."


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
        tools=[{"type": "function", "function": {"name": "log_meal"}}],
        tool_executor=execute, on_tool_event=on_event,
    )
    assert calls[0]["tool_choice"] == "required"
    assert {tool["function"]["name"] for tool in calls[0]["tools"]} == {
        "respond_without_tool", "log_meal",
    }
    assert (answer, used, log) == ("Je peux t’aider.", None, [])
    assert events == [("respond_without_tool", "started"), ("respond_without_tool", "finished")]


@pytest.mark.asyncio
async def test_selected_meal_tool_writes_and_reports_actual_result(db_session, monkeypatch):
    user = User(telegram_id=90901, first_name="Test")
    db_session.add(user)
    await db_session.flush()
    calls = []
    events = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _response([_tool_call(
                "log_meal",
                '{"entry_type":"meal","meal_slot":"breakfast","estimated_calories":220}',
            )])
        return _response(content="C'est noté, total 2170 cal")

    monkeypatch.setattr(
        "app.llm.chat_client._get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    async def execute(name, args):
        return await _tool_log_meal(
            args, user, db_session, "J'ai mangé du fromage blanc et une banane",
        )

    async def on_event(name, status):
        events.append((name, status))

    answer, _, _, _, log = await run_agentic_loop(
        system="test", messages=[{"role": "user", "content": "J'ai mangé..."}],
        tools=[{"type": "function", "function": {"name": "log_meal"}}],
        tool_executor=execute, on_tool_event=on_event,
    )

    assert calls[0]["tool_choice"] == "required"
    assert calls[1]["tool_choice"] == "auto"
    assert events == [("log_meal", "started"), ("log_meal", "finished")]
    assert log[0]["result"]["ok"] is True
    assert _verify_action_claim(answer, log) == (
        "Repas enregistré. Le détail exact est juste en dessous."
    )
    totals = await meal_entry_repo.daily_totals(db_session, user.id, date.today(), date.today())
    assert [(t.total_calories, t.entry_count) for t in totals] == [(220, 1)]


@pytest.mark.asyncio
async def test_meal_is_saved_on_paris_day_even_if_server_day_differs(db_session, monkeypatch):
    user = User(telegram_id=90902, first_name="Test")
    db_session.add(user)
    await db_session.flush()
    monkeypatch.setattr("app.llm.chat._nutrition_today", lambda: date(2026, 9, 24))

    result = await _tool_log_meal(
        {"entry_type": "meal", "estimated_calories": 220},
        user, db_session, "J'ai mangé du fromage blanc et une banane",
    )
    assert result["entry_date"] == "2026-09-24"
    totals = await meal_entry_repo.daily_totals(
        db_session, user.id, date(2026, 9, 24), date(2026, 9, 24),
    )
    assert totals[0].total_calories == 220
