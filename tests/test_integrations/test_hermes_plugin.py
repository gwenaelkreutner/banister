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
    assert not {n for n in names if any(w in n for w in ("approve", "publish_", "delete"))}
    assert "banister_propose_session" in names


def test_manifest_lists_exactly_the_registered_tools():
    manifest = Path(tools.__file__).parent / "plugin.yaml"
    listed = {
        line.strip()[2:] for line in manifest.read_text().splitlines() if line.startswith("  - ")
    }
    assert listed == {schema["name"] for schema, _ in tools.TOOLS}


def test_propose_sends_a_card_and_keeps_it_out_of_the_model_reply(monkeypatch):
    calls, cards = [], []

    def fake_call(method, path, body=None, **_kw):
        calls.append((method, path, body))
        return {
            "proposal_id": "abc",
            "tss_estimate": 140.0,
            "steps": [],
            "card_text": "CARD",
            "card_buttons": {"approve": "Yes", "decline": "No"},
        }

    monkeypatch.setattr(tools, "call", fake_call)
    tools.send_card = lambda text, buttons: cards.append((text, buttons)) or True

    result = json.loads(
        _tool("banister_propose_session")({"date": "2026-11-07", "template_id": "x", "title": ""})
    )

    assert calls == [("POST", "/proposals", {"date": "2026-11-07", "template_id": "x"})]
    assert cards == [("CARD", [("Yes", "ok:abc"), ("No", "no:abc")])]
    assert result["card_sent"] is True and "card_text" not in result
    assert tools.retrieved_values()[0]["result"]["tss_estimate"] == 140.0


def test_no_gateway_means_no_card_and_the_agent_is_told(monkeypatch):
    monkeypatch.setattr(
        tools,
        "call",
        lambda *a, **k: {
            "proposal_id": "abc",
            "card_text": "CARD",
            "card_buttons": {"approve": "Y", "decline": "N"},
        },
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
