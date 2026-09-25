"""RPE (Rate of Perceived Exertion) — échelle standard 1-10 (type Borg CR10), 2026-09-21.

Deux sources possibles, jamais confondues (voir CLAUDE.md § RPE) :
  - intervals.icu (`icu_rpe`) — consommé tel quel, jamais recalculé (Principe IV).
  - Le clavier Telegram (`app/bot/keyboards/session_log.py`) propose les 10 valeurs
    exactes de cette même échelle ; le chiffre choisi est enregistré tel quel.

Les bandes HARD/EASY ci-dessous sont un JUGEMENT — aucune échelle publiée ne tombe pile
sur ces bornes à 3 catégories, documenté comme tel plutôt que présenté comme une norme
(même statut que ACWR_MIN_CTL, app/engine/guardrail_thresholds.py).
"""
from __future__ import annotations

from app.core.localization import t

RPE_HARD_MIN = 7.0
RPE_EASY_MAX = 3.0

RPE_SCALE_LABELS_FR = (
    "Aucun effort",
    "Très facile",
    "Facile",
    "Confortable",
    "Légèrement difficile",
    "Assez difficile",
    "Difficile",
    "Très difficile",
    "Extrêmement difficile",
    "Effort maximal",
)

_RPE_SCALE_KEYS = (
    "engine.rpe_scale.no_effort",
    "engine.rpe_scale.very_easy",
    "engine.rpe_scale.easy",
    "engine.rpe_scale.comfortable",
    "engine.rpe_scale.slightly_hard",
    "engine.rpe_scale.fairly_hard",
    "engine.rpe_scale.hard",
    "engine.rpe_scale.very_hard",
    "engine.rpe_scale.extremely_hard",
    "engine.rpe_scale.maximal_effort",
)

_EMOJI_BY_BAND = {"hard": "😫", "normal": "😐", "easy": "🙂"}
_LABEL_FR_BY_BAND = {"hard": "dur", "normal": "modéré", "easy": "facile"}
_LABEL_KEY_BY_BAND = {
    "hard": "engine.rpe_band.hard",
    "normal": "engine.rpe_band.normal",
    "easy": "engine.rpe_band.easy",
}


def rpe_scale_labels(*, language: str | None = None) -> tuple[str, ...]:
    """Localized variant of `RPE_SCALE_LABELS_FR` for the Telegram keyboard — same
    10 ordered choices, in the installation language."""
    return tuple(t(key, language=language) for key in _RPE_SCALE_KEYS)


def rpe_band(rpe: float) -> str:
    """"hard" | "normal" | "easy" — dérivé du chiffre, jamais stocké comme tel."""
    if rpe >= RPE_HARD_MIN:
        return "hard"
    if rpe <= RPE_EASY_MAX:
        return "easy"
    return "normal"


def rpe_emoji(rpe: float | None) -> str:
    """Emoji compact pour l'affichage (historique /forme, résumé semaine du chat).
    "—" si pas de ressenti, jamais un emoji par défaut qui laisserait croire à une
    donnée mesurée."""
    if rpe is None:
        return "—"
    return _EMOJI_BY_BAND[rpe_band(rpe)]


def rpe_label(rpe: float, *, language: str | None = None) -> str:
    """Libellé pour le contexte LLM — le chiffre brut reste la donnée, ce libellé
    n'est qu'un repère de lecture à côté."""
    value = f"{rpe:.0f}" if float(rpe).is_integer() else f"{rpe:.1f}"
    band_label = t(_LABEL_KEY_BY_BAND[rpe_band(rpe)], language=language)
    return f"{value}/10 ({band_label})"
