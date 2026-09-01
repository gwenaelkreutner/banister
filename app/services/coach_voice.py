"""Resolve which coach voice to speak in (spec 007 US5, FR-024, FR-026).

Voice is athlete state: `users.coach_voice`, falling back to the deployer's
`settings.persona`, falling back to a guaranteed-present `coach-default`. This helper is
the single place that knows the fallback chain — `app/llm/` calls it rather than
reaching into `app/core/persona.py` directly (Constitution III, mirroring how spec 006
kept verification out of `llm/`).

Populated in US5 (T042).
"""
from __future__ import annotations
