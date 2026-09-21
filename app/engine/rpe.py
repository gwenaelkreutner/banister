"""RPE (Rate of Perceived Exertion) — échelle standard 1-10 (type Borg CR10), 2026-09-21.

Deux sources possibles, jamais confondues (voir CLAUDE.md § RPE) :
  - intervals.icu (`icu_rpe`) — consommé tel quel, jamais recalculé (Principe IV).
  - Le clavier Telegram 3 boutons (😫/😐/🙂, `app/bot/keyboards/session_log.py`) —
    l'athlète choisit une bande large, pas un chiffre précis ; chaque bouton écrit une
    valeur représentative (`RPE_EASY_VALUE`/`RPE_NORMAL_VALUE`/`RPE_HARD_VALUE`) sur la
    même échelle 1-10, pour que `session_logs.rpe` reste un seul type de donnée partout,
    jamais une chaîne catégorielle à côté d'un nombre.

Les bandes HARD/EASY ci-dessous sont un JUGEMENT — aucune échelle publiée ne tombe pile
sur ces bornes à 3 catégories, documenté comme tel plutôt que présenté comme une norme
(même statut que ACWR_MIN_CTL, app/engine/guardrail_thresholds.py).
"""
from __future__ import annotations

RPE_HARD_MIN = 7.0
RPE_EASY_MAX = 3.0

# Valeurs représentatives écrites par les 3 boutons Telegram — reprend les valeurs déjà
# choisies par app/engine/tss.py::detect_fatigue_anomaly_scalar() (ex-`RPE_EMOJI_INT_MAP`,
# retiré au profit de ce module unique) plutôt que d'en inventer de nouvelles.
RPE_EASY_VALUE = 3
RPE_NORMAL_VALUE = 5
RPE_HARD_VALUE = 8

_EMOJI_BY_BAND = {"hard": "😫", "normal": "😐", "easy": "🙂"}
_LABEL_FR_BY_BAND = {"hard": "dur", "normal": "modéré", "easy": "facile"}


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


def rpe_label(rpe: float) -> str:
    """Libellé FR pour le contexte LLM — le chiffre brut reste la donnée, ce libellé
    n'est qu'un repère de lecture à côté."""
    value = f"{rpe:.0f}" if float(rpe).is_integer() else f"{rpe:.1f}"
    return f"{value}/10 ({_LABEL_FR_BY_BAND[rpe_band(rpe)]})"
