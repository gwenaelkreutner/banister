"""Resolve which coach voice to speak in (spec 007 US5, FR-024, FR-026).

Voice is athlete state: `users.coach_voice`, falling back to the deployer's
`settings.persona`, falling back to a guaranteed-present `coach-default`. This helper is
the single place that knows the fallback chain — `app/llm/` calls it rather than
reaching into `app/core/persona.py` directly (Constitution III, mirroring how spec 006
kept verification out of `llm/`).
"""
from __future__ import annotations

from app.config import settings
from app.core.exceptions import PersonaNotFoundError
from app.core.persona import PERSONAS_DIR, Persona, load_persona

_FALLBACK_ID = "coach-default"


def available_voices() -> list[Persona]:
    """Every persona the deployer ships, for the `/voice` menu (FR-022). Files that fail
    to load are skipped rather than breaking the list."""
    out: list[Persona] = []
    for path in sorted(PERSONAS_DIR.glob("*.yaml")):
        try:
            out.append(load_persona(path.stem))
        except PersonaNotFoundError:
            continue
    return out


def resolve_voice(user) -> tuple[Persona, bool]:
    """`(persona, fell_back)`. Tries `user.coach_voice`, then `settings.persona`, then
    `coach-default`. `fell_back` is True when the requested id did not resolve — the
    caller prepends a one-line notice (FR-026)."""
    requested = getattr(user, "coach_voice", None) or settings.persona
    try:
        return load_persona(requested), False
    except PersonaNotFoundError:
        pass
    try:
        return load_persona(settings.persona), requested != settings.persona
    except PersonaNotFoundError:
        return load_persona(_FALLBACK_ID), True


VOICE_FALLBACK_NOTICE = (
    "(la voix demandée est introuvable, je reprends la voix par défaut)\n\n"
)
