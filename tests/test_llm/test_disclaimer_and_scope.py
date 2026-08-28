"""The athlete knows what this is not (spec 006 US6, FR-028–FR-030, SC-009)."""
from __future__ import annotations

import inspect

from app.bot.routers import setup as setup_router
from app.llm.prompts import DISCLAIMER_TEXT, SCOPE_OF_ADVICE_RULES, build_ux_system_prompt

# ── FR-028 / SC-009: the disclaimer is shown before the first coaching interaction ──


def test_disclaimer_states_what_the_product_is_not():
    low = DISCLAIMER_TEXT.lower()
    assert "médecin" in low
    assert "certifié" in low or "entraîneur" in low
    assert "suggestion" in low  # sessions are suggestions
    assert "décides" in low or "décide" in low


def test_finalize_setup_emits_the_disclaimer():
    """A grep, not a vibe — _finalize_setup must send DISCLAIMER_TEXT (SC-009). There is
    no first-run flow yet (spec 007); the end of /setup is where a first use passes
    today (research R7)."""
    src = inspect.getsource(setup_router._finalize_setup)
    assert "DISCLAIMER_TEXT" in src
    assert "message.answer(DISCLAIMER_TEXT" in src.replace(" ", "")


def test_readme_carries_the_disclaimer():
    from pathlib import Path

    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8").lower()
    assert "not a physician" in text and "not a certified coach" in text
    assert "suggestion" in text


# ── FR-029 / FR-030: scope-of-advice rules are in every system prompt ────────


def test_scope_rules_cover_referral_and_no_diagnosis():
    low = SCOPE_OF_ADVICE_RULES.lower()
    assert "médecin" in low
    assert "consulter" in low or "avis médical" in low   # FR-029: refer, don't coach through
    assert "diagnostic" in low                            # FR-030: no diagnosis


def test_build_ux_system_prompt_includes_the_scope_rules():
    for lvl in (0, 1, 2):
        prompt = build_ux_system_prompt(lvl)
        assert "diagnostic" in prompt.lower()
        assert "médecin" in prompt.lower()
