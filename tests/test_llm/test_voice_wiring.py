"""Coach voice resolution and wiring (spec 007 US5, FR-023..FR-026)."""
from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.llm.prompts import build_ux_system_prompt
from app.services.coach_voice import available_voices, resolve_voice


def _user(voice):
    return SimpleNamespace(coach_voice=voice)


def test_resolve_voice_falls_back_through_the_chain():
    p, fell = resolve_voice(_user("marseillais"))
    assert p.id == "marseillais" and fell is False

    p, fell = resolve_voice(_user(None))
    assert p.id == settings.persona and fell is False

    p, fell = resolve_voice(_user("does-not-exist"))
    assert p.id == "coach-default" and fell is True


def test_removed_voice_selections_map_to_coach():
    for legacy_id in ("pace", "zen", "analyste"):
        p, fell = resolve_voice(_user(legacy_id))
        assert p.id == "coach-default" and fell is False


def test_available_voices_lists_the_three_shipped_personas():
    voices = available_voices()
    assert {v.id for v in voices} == {"coach-default", "marseillais", "pedagogue"}
    assert all(v.voice for v in voices)
    assert all(v.language == "fr" for v in voices)


def test_ux_prompt_follows_the_selected_persona():
    marseillais, _ = resolve_voice(_user("marseillais"))
    pedagogue, _ = resolve_voice(_user("pedagogue"))

    marseillais_prompt = build_ux_system_prompt(2, persona=marseillais)
    pedagogue_prompt = build_ux_system_prompt(2, persona=pedagogue)

    assert marseillais_prompt != pedagogue_prompt
    assert "marseillais" in marseillais_prompt.lower()
    assert "pourquoi" in pedagogue_prompt.lower()
    assert "diagnostic" in pedagogue_prompt.lower()


def test_ux_prompt_without_a_persona_uses_coach():
    prompt = build_ux_system_prompt(2)
    assert "Tu t'appelles Coach" in prompt
    assert "diagnostic" in prompt.lower()
