"""Coach voice resolution and wiring (spec 007 US5, FR-023..FR-026)."""
from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.llm.prompts import build_ux_system_prompt
from app.services.coach_voice import available_voices, resolve_voice


def _user(voice):
    return SimpleNamespace(coach_voice=voice)


def test_resolve_voice_falls_back_through_the_chain():
    # explicit selection
    p, fell = resolve_voice(_user("zen"))
    assert p.id == "zen" and fell is False

    # None → deployer default (settings.persona == "pace")
    p, fell = resolve_voice(_user(None))
    assert p.id == settings.persona and fell is False

    # unresolvable → coach-default, and the caller is told (FR-026)
    p, fell = resolve_voice(_user("does-not-exist"))
    assert p.id in ("pace", "coach-default") and fell is True


def test_available_voices_lists_the_shipped_personas_with_descriptors():
    ids = {v.id for v in available_voices()}
    assert {"coach-default", "pace", "zen", "analyste"} <= ids
    assert all(v.voice for v in available_voices())  # FR-022 — a descriptor each
    assert all(v.language == "fr" for v in available_voices())


def test_ux_prompt_follows_the_selected_persona():
    pace, _ = resolve_voice(_user("pace"))
    analyste, _ = resolve_voice(_user("analyste"))

    pace_prompt = build_ux_system_prompt(2, persona=pace)
    analyste_prompt = build_ux_system_prompt(2, persona=analyste)

    assert pace_prompt != analyste_prompt
    assert "analyste" in analyste_prompt.lower()
    assert "pote expert" in pace_prompt.lower() or "chaleureux" in pace_prompt.lower()
    # spec 006 rules survive the persona swap
    assert "diagnostic" in analyste_prompt.lower()


def test_ux_prompt_without_a_persona_is_the_historical_pace_text():
    prompt = build_ux_system_prompt(2)
    assert "Tu t'appelles Pace" in prompt
    assert "diagnostic" in prompt.lower()  # spec 006 rules still there
