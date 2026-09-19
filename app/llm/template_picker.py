"""Second, isolated LLM call that maps an athlete's free-text style preference onto one
template id from a closed candidate list.

Kept out of the chat's tool schema on purpose: the full template catalogue is ~9k chars
and was being resent on every freestyle chat turn as the description of a `template_id`
enum. Here it is only ever sent when the athlete actually voiced a preference, and only
the candidates of the already-resolved workout type are shown (2–13 templates, not 18).

Zero training-load computation happens here (Constitution Principle I): the model
returns an id, `fitting.fit_template()` still does the math downstream. An answer that
is not one of the candidate ids is treated as "no pick" — the caller then falls back to
the deterministic rotation exactly as if no preference had been given.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.engine.session_library import SessionTemplate

logger = logging.getLogger(__name__)

NO_PICK = "NONE"

_SYSTEM_PROMPT = (
    "Tu aides un coach cycliste à choisir UNE séance dans une liste fermée.\n"
    "On te donne la préférence exprimée par l'athlète et la liste des séances candidates "
    "(id, objectif, contenu, pour qui).\n"
    "Réponds UNIQUEMENT par l'id exact de la séance qui correspond le mieux à la "
    f"préférence, ou par {NO_PICK} si aucune ne correspond clairement.\n"
    "Aucun autre texte, aucune ponctuation, aucune explication."
)


def _format_candidates(candidates: list[SessionTemplate]) -> str:
    return "\n".join(
        f"- {t.id} : {t.purpose} — {t.intent} — {t.suits}" for t in candidates
    )


async def pick_template(
    style_preference: str,
    candidates: list[SessionTemplate],
) -> str | None:
    """Returns a candidate id, or `None` when the model declines or answers off-list.
    Never raises on model failure — a preference is a nice-to-have, the deterministic
    default suggestion must still be produced."""
    if not style_preference.strip() or not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].id

    valid_ids = {t.id for t in candidates}
    user_message = (
        f"Préférence de l'athlète : {style_preference.strip()}\n\n"
        f"Séances candidates :\n{_format_candidates(candidates)}"
    )

    try:
        from app.llm.factory import get_provider
        # Output is one id, but reasoning models may spend hidden tokens first — a tight
        # cap yields null content (see CLAUDE.md, LLM_MAX_TOKENS), so reuse the shared budget.
        raw = await get_provider().generate(
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=settings.llm_max_tokens,
        )
    except Exception as exc:
        logger.warning("template_picker: appel LLM échoué, repli sur la rotation — %s", exc)
        return None

    answer = (raw or "").strip().strip("`'\".").strip()
    if answer.upper() == NO_PICK:
        return None
    if answer not in valid_ids:
        logger.info("template_picker: réponse hors liste ignorée — %.80r", answer)
        return None
    return answer
