"""Synthèse `/review` — un seul appel LLM one-shot (comme template_picker.py/narrator.py),
jamais la boucle agentique : app/services/session_review.py assemble déjà toutes les
données nécessaires, le LLM ne fait ici que rédiger (Principe I — zéro calcul de charge).
"""
from __future__ import annotations

import logging

from app.config import settings
from app.llm.prompts import build_review_system_prompt, build_review_user_message
from app.services.session_review import ReviewContext

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = (
    "⚠️ Je n'ai pas pu rédiger la synthèse pour l'instant — réessaie dans un instant."
)


async def generate_session_review(ctx: ReviewContext) -> str:
    system = build_review_system_prompt(has_rpe=ctx.log.rpe_emoji is not None)
    user_message = build_review_user_message(ctx)

    try:
        from app.llm.factory import get_provider

        raw = await get_provider().generate(
            system_prompt=system,
            user_message=user_message,
            max_tokens=settings.llm_max_tokens,
        )
    except Exception as exc:
        logger.warning("generate_session_review: appel LLM échoué — %s", exc)
        return _FALLBACK_TEXT

    return (raw or "").strip() or _FALLBACK_TEXT
