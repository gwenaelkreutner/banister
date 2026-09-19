"""app/llm/template_picker.py — the isolated second LLM call that maps a free-text
style preference onto one template id from a closed candidate list.

The provider is faked at `app.llm.factory.get_provider` (picker imports it at call
time). Every branch must degrade to `None` rather than raise: a preference is optional,
the deterministic suggestion must always be produced.
"""
from __future__ import annotations

import pytest

import app.llm.factory as factory_mod
from app.engine.freestyle_selector import candidates_for
from app.llm.template_picker import NO_PICK, pick_template


class _FakeProvider:
    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.calls: list[dict] = []

    async def generate(self, system_prompt: str, user_message: str, max_tokens: int = 600) -> str:
        self.calls.append({"system": system_prompt, "user": user_message, "max_tokens": max_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def intervals():
    return candidates_for("intervals")


def _install(monkeypatch, provider: _FakeProvider) -> None:
    monkeypatch.setattr(factory_mod, "get_provider", lambda: provider)


async def test_valid_id_is_returned(monkeypatch, intervals):
    provider = _FakeProvider("vo2-5x5")
    _install(monkeypatch, provider)

    assert await pick_template("des efforts de 5 min", intervals) == "vo2-5x5"


async def test_answer_is_trimmed_of_quotes_and_whitespace(monkeypatch, intervals):
    provider = _FakeProvider("  `vo2-5x5`.\n")
    _install(monkeypatch, provider)

    assert await pick_template("des efforts de 5 min", intervals) == "vo2-5x5"


async def test_none_sentinel_returns_none(monkeypatch, intervals):
    _install(monkeypatch, _FakeProvider(NO_PICK))

    assert await pick_template("du yoga", intervals) is None


async def test_off_list_answer_returns_none(monkeypatch, intervals):
    """An id that exists in the library but is not a candidate of this workout type
    (recovery-z1 is a recovery template) is off-list here and must be dropped."""
    _install(monkeypatch, _FakeProvider("recovery-z1"))

    assert await pick_template("tranquille", intervals) is None


async def test_provider_failure_returns_none(monkeypatch, intervals):
    _install(monkeypatch, _FakeProvider(RuntimeError("boom")))

    assert await pick_template("des efforts courts", intervals) is None


async def test_prompt_only_lists_the_given_candidates(monkeypatch, intervals):
    """The whole point of the second call: only the resolved type's templates are sent,
    never the full library."""
    provider = _FakeProvider("vo2-5x5")
    _install(monkeypatch, provider)

    await pick_template("des efforts de 5 min", intervals)

    user_msg = provider.calls[0]["user"]
    assert "vo2-5x5" in user_msg
    assert "recovery-z1" not in user_msg
    assert "long-ride-z2" not in user_msg
    assert "des efforts de 5 min" in user_msg


async def test_empty_preference_skips_the_call(monkeypatch, intervals):
    provider = _FakeProvider("vo2-5x5")
    _install(monkeypatch, provider)

    assert await pick_template("   ", intervals) is None
    assert provider.calls == []


async def test_single_candidate_skips_the_call(monkeypatch):
    provider = _FakeProvider("should-not-be-called")
    _install(monkeypatch, provider)
    only = candidates_for("recovery")
    assert len(only) == 1

    assert await pick_template("tranquille", only) == only[0].id
    assert provider.calls == []
