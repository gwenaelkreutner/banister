"""Coach persona loading (spec 007 US5).

An earlier research draft claimed `load_persona()` was broken; a direct test proved it
works. This file is the regression guard so a persona file cannot be shipped in a state
the loader rejects.
"""
from __future__ import annotations

import pytest

from app.core.exceptions import PersonaNotFoundError
from app.core.persona import PERSONAS_DIR, load_persona


def _shipped_ids() -> list[str]:
    return sorted(p.stem for p in PERSONAS_DIR.glob("*.yaml"))


def test_every_shipped_persona_loads():
    ids = _shipped_ids()
    assert ids, "no persona files found"
    for pid in ids:
        p = load_persona(pid)
        assert p.system_prompt.strip(), f"{pid}: empty system_prompt"
        assert p.ux_prompt.strip(), f"{pid}: empty ux_prompt"
        assert p.name and p.language


def test_pace_is_the_french_default_voice():
    p = load_persona("pace")
    assert p.language == "fr"
    assert "Pace" in p.name or "pace" in p.id


def test_coach_default_always_resolves():
    """The FR-026 fallback target — must exist and load."""
    assert load_persona("coach-default").system_prompt


def test_unknown_id_raises():
    with pytest.raises(PersonaNotFoundError):
        load_persona("does-not-exist")


def test_persona_formatting_fills_first_name():
    p = load_persona("pace")
    out = p.format_system_prompt(first_name="Gwen")
    assert "Gwen" in out
    assert "{first_name}" not in out
