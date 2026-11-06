"""integrations/hermes/banister: the plugin stays a thin adapter and never gets a write path."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations" / "hermes"))

from banister import client as plugin_client  # noqa: E402
from banister import tools  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    tools._retrieved.clear()
    tools.send_card = None


def _tool(name):
    return next(handler for schema, handler in tools.TOOLS if schema["name"] == name)


def test_the_model_has_no_tool_that_publishes_or_withdraws():
    names = {schema["name"] for schema, _ in tools.TOOLS}
    assert not {
        n for n in names if any(w in n for w in ("approve", "publish_", "delete", "action"))
    }
    assert "banister_propose_session" in names


def test_no_tool_reaches_the_action_route(monkeypatch):
    """Whatever a tool does, it never touches what only the athlete's tap may touch."""
    seen = []

    def fake_call(method, path, body=None, **_kw):
        seen.append(path)
        return {}

    monkeypatch.setattr(tools, "call", fake_call)
    args = {
        "date": "2026-11-07",
        "metrics": ["hrv"],
        "category": "event",
        "note": "x",
        "publication_id": "x",
        "goal": "fitness",
        "reason": "fatigue",
        "modification_type": "reduce_volume",
        "week_offset": 0,
        "constraint_type": "personal",
        "day_offset": 0,
        "action": "skip",
        "location": "knee",
        "severity": "mild",
        "estimated_recovery_days": 3,
    }
    for _schema, handler in tools.TOOLS:
        handler(dict(args))
    assert seen and not [p for p in seen if p.startswith("/actions")]


def test_manifest_lists_exactly_the_registered_tools():
    manifest = Path(tools.__file__).parent / "plugin.yaml"
    listed = {
        line.strip()[2:] for line in manifest.read_text().splitlines() if line.startswith("  - ")
    }
    assert listed == {schema["name"] for schema, _ in tools.TOOLS}


def test_propose_sends_a_card_and_keeps_it_out_of_the_model_reply(monkeypatch):
    calls, cards = [], []
    card = {"text": "CARD", "buttons": [[{"label": "Yes", "action": "proposal:approve:abc"}]]}

    def fake_call(method, path, body=None, **_kw):
        calls.append((method, path, body))
        return {"proposal_id": "abc", "tss_estimate": 140.0, "steps": [], "card": card}

    monkeypatch.setattr(tools, "call", fake_call)
    tools.send_card = lambda c: cards.append(c) or True

    result = json.loads(
        _tool("banister_propose_session")({"date": "2026-11-07", "template_id": "x", "title": ""})
    )

    assert calls == [("POST", "/proposals", {"date": "2026-11-07", "template_id": "x"})]
    assert cards == [card]
    assert result["card_sent"] is True and "card" not in result
    assert tools.retrieved_values()[0]["result"]["tss_estimate"] == 140.0


def test_no_gateway_means_no_card_and_the_agent_is_told(monkeypatch):
    monkeypatch.setattr(
        tools, "call", lambda *a, **k: {"proposal_id": "abc", "card": {"text": "C"}}
    )
    assert (
        json.loads(_tool("banister_propose_session")({"date": "2026-11-07"}))["card_sent"] is False
    )


def test_api_errors_reach_the_model_as_data(monkeypatch):
    def fail(*_a, **_k):
        raise plugin_client.BanisterError(422, "unknown template_id 'x'")

    monkeypatch.setattr(tools, "call", fail)
    assert json.loads(_tool("banister_evaluate_session")({"date": "2026-11-07"})) == {
        "error": "unknown template_id 'x'"
    }


def test_buttons_fit_telegram_and_stay_opaque():
    """Telegram refuses callback data over 64 bytes, prefix included."""
    from uuid import uuid4

    from banister.messaging import CALLBACK_PREFIX, _keyboard

    longest = [
        f"log:rpe:{uuid4()}:skip",
        f"publication:withdraw:{uuid4()}",
        f"calendar:approve:{uuid4()}",
    ]
    assert all(len((CALLBACK_PREFIX + a).encode()) <= 64 for a in longest)
    pytest.importorskip("telegram")
    markup = _keyboard([[{"label": "A", "action": longest[0]}]])
    assert markup.inline_keyboard[0][0].callback_data == CALLBACK_PREFIX + longest[0]


def test_the_skill_stays_editable_by_the_agent():
    """A skill registered by a plugin is read-only for the agent's skill_manage."""
    root = Path(tools.__file__).parents[1]
    assert "register_skill" not in (root / "banister" / "__init__.py").read_text()
    skill = (root / "skills" / "banister-coach" / "SKILL.md").read_text()
    assert "\nname: banister-coach\n" in skill  # must equal the folder name


@pytest.mark.parametrize("accepted, sent", [(True, []), (False, [{"text": "HINT"}])])
async def test_a_refused_handover_is_never_silent(monkeypatch, accepted, sent):
    import asyncio

    from banister import messaging

    class Ctx:
        def inject_message(self, *_a, **_k):
            return accepted

    out = []

    async def fake_send(message):
        out.append(message)
        return True

    monkeypatch.setitem(messaging._state, "ctx", Ctx())
    monkeypatch.setattr(messaging, "owner_chat_id", lambda: "1")
    monkeypatch.setattr(messaging, "_send", fake_send)
    await messaging._hand_over(
        asyncio.get_running_loop(), {"agent_prompt": "go", "handover_hint": "HINT"}
    )
    assert out == sent


def test_session_key_follows_the_profile(monkeypatch):
    from banister import messaging

    monkeypatch.setattr(messaging, "__file__", "/h/profiles/coach/plugins/banister/messaging.py")
    assert messaging._session_key("1") == "agent:coach:telegram:dm:1"
    monkeypatch.setattr(messaging, "__file__", "/h/.hermes/plugins/banister/messaging.py")
    assert messaging._session_key("1") == "agent:main:telegram:dm:1"


def test_the_reply_is_observed_and_never_rewritten(monkeypatch):
    import banister

    registered = {}

    class Ctx:
        def register_tool(self, **_k):
            pass

        def register_platform_handler(self, *_a):
            pass

        def register_hook(self, name, callback):
            registered[name] = callback

    banister.register(Ctx())
    assert set(registered) == {"post_llm_call"}  # not the transform hook, which can replace text

    seen = []
    monkeypatch.setattr(banister, "call", lambda *a, **k: seen.append(a[1]))
    assert registered["post_llm_call"](assistant_response="CTL 40", session_id="s") is None
    assert seen == ["/verify-reply"]

    def down(*_a, **_k):
        raise plugin_client.BanisterError(0, "down")

    monkeypatch.setattr(banister, "call", down)
    assert registered["post_llm_call"](assistant_response="CTL 40") is None  # never breaks a turn
