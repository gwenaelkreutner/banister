"""
Conversion du markdown minimal que le LLM peut produire (**gras**) vers de vraies
balises HTML Telegram — jusqu'ici le texte était seulement html.escape()-é, donc les
`**mot**` du modèle s'affichaient tels quels au lieu d'un vrai gras (parse_mode="HTML"
n'interprète aucune syntaxe markdown). Approche standard des bots LLM-vers-Telegram :
garder HTML (déjà la convention de tout le reste du bot, cf. les <b> écrits à la main
dans plan.py/setup.py/etc.), convertir seulement le sous-ensemble markdown autorisé
par les prompts (app/llm/prompts.py, personas/*.yaml — gras seul, jamais de liste/lien/
italique).
"""
import re
from html import escape

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)


def to_telegram_html(text: str) -> str:
    """Échappe le texte brut puis convertit **gras**/__gras__ en <b>...</b>.
    L'échappement HTML précède la conversion : les astérisques/underscores ne sont
    jamais touchés par escape(), donc aucun risque de double-échapper les balises
    <b> insérées ensuite."""
    escaped = escape(text)
    return _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", escaped)
