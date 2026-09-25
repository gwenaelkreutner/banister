"""Fencing anti-injection pour les données mémorisées et réinjectées dans le system
prompt (coach_memory, athlete_notes) — écrites par le LLM lui-même via l'outil
update_coach_memory (app/llm/chat.py::_tool_update_coach_memory), donc potentiellement
adversariales, et réinjectées telles quelles à CHAQUE tour de chat suivant
(app/llm/tools.py::build_system_prompt). coach_memory.note est tronqué à l'écriture
(120 caractères) mais athlete_notes (clé/valeur) ne l'est pas — la troncature ci-dessous
est le seul filet de sécurité pour ce champ, et couvre aussi les notes déjà stockées
avant l'ajout de cette protection.
"""

from app.core.localization import t

FENCE_RULE = t("llm.fence.rule")
FENCE_OPEN = t("llm.fence.open")
FENCE_CLOSE = t("llm.fence.close")
MAX_BLOCK_CHARS = 4000


def sanitize_untrusted_text(text: str) -> str:
    """Neutralise toute tentative de forger les marqueurs de fence dans un texte stocké."""
    if not text:
        return text
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def wrap_untrusted_block(lines: list[str]) -> list[str]:
    """Encadre un bloc déjà formé (mémoire coach / notes athlète) entre les marqueurs de
    fence, avec troncature dure en dernier filet de sécurité."""
    body = "\n".join(lines)
    if len(body) > MAX_BLOCK_CHARS:
        body = body[:MAX_BLOCK_CHARS] + t("llm.fence.truncated")
    return [FENCE_RULE, FENCE_OPEN, body, FENCE_CLOSE]
