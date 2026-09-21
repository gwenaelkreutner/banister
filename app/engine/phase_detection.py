"""`phase_detection` — classification diagnostique de la phase d'entraînement, PAS la
phase prescriptive de `app/engine/periodization.py` (`week.phase`, qui construit le plan).

Porté depuis un script tiers (Section11, fourni par l'utilisateur) — leur système fait
~700 lignes de règles à deux flux ; cette v1 Banister garde le flux principal
(comportement récent) comme cœur déterministe et traite le flux secondaire (phase
déclarée par le plan actif, ou proximité de la date cible) comme un simple recoupement
best-effort — PAS une parité ligne à ligne avec le script source.

Distinction volontaire de vocabulaire : même 4 mots (base/build/peak/taper, intuitifs à
lire) que la phase prescriptive, mais jamais nommé `phase` ici — toujours
`detected_phase`, pour ne jamais entrer en collision avec `week.phase` partout où les
deux pourraient apparaître ensemble (prompt du chat, `narrator.py`, `tools.py`).

Zéro LLM (Principe I) — fonctions pures sur des listes `SessionLog`/`Activity` déjà
chargées, réutilise `compute_weekly_snapshot()` pour la tendance de charge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.engine.weekly_snapshot import compute_weekly_snapshot

DetectedPhase = Literal["base", "build", "peak", "taper"]
Confidence = Literal["low", "medium", "high"]

_VALID_PHASES = ("base", "build", "peak", "taper")

# Même seuil que app/engine/freestyle_selector.py::_HARD_EFFORT_TSS_PER_HOUR (100
# TSS/heure = puissance seuil par définition, Coggan) — précédent déjà établi dans le
# projet, pas réinventé ici.
_HARD_EFFORT_TSS_PER_HOUR = 70.0

# Seuils du flux principal (comportemental) — ADAPTATION propre à ce module, pas des
# valeurs publiées : aucune littérature identifiée pour ces bornes précises de détection
# de phase depuis la seule tendance de charge. Documentés comme jugement, même statut que
# ACWR_MIN_CTL (app/engine/guardrail_thresholds.py).
_TAPER_LOAD_DROP_PCT = -25.0
_BUILD_LOAD_RISE_PCT = 20.0
_PEAK_MIN_HARD_DAYS_7D = 2

# Seuils du flux secondaire (proximité de la date cible) — même statut, jugement.
_TARGET_DATE_TAPER_DAYS = 10
_TARGET_DATE_PEAK_DAYS = 28
_TARGET_DATE_BUILD_DAYS = 56


@dataclass(frozen=True)
class PhaseDetectionResult:
    detected_phase: DetectedPhase
    confidence: Confidence
    reason_codes: list[str]
    secondary_phase: DetectedPhase | None
    streams_agree: bool | None  # None si le flux secondaire est indisponible


def _hard_days_in_window(items: list, *, today: date, window_days: int) -> int:
    """Duck-type sur SessionLog/Activity (même pattern que
    `freestyle_selector.py::days_since_hard_effort`, mais compte les jours dans la
    fenêtre plutôt que le plus récent)."""
    count = 0
    for it in items:
        tss = getattr(it, "tss_actual", None) or getattr(it, "tss", None)
        if not tss:
            continue
        duration_min = getattr(it, "duration_minutes_actual", None)
        if duration_min is None:
            duration_s = getattr(it, "duration_seconds", None)
            duration_min = duration_s / 60 if duration_s else None
        if not duration_min:
            continue
        if (tss / (duration_min / 60)) < _HARD_EFFORT_TSS_PER_HOUR:
            continue
        item_date = getattr(it, "logged_date", None) or getattr(it, "activity_date", None)
        if item_date is None:
            continue
        gap = (today - item_date).days
        if 0 <= gap < window_days:
            count += 1
    return count


def _primary_phase(logs: list, *, today: date) -> tuple[DetectedPhase, Confidence, list[str]]:
    snapshot = compute_weekly_snapshot(logs, today)
    hard_days = _hard_days_in_window(logs, today=today, window_days=7)

    if snapshot.tss_7d <= 0:
        return "base", "low", ["no_training_last_7d"]

    trend = snapshot.load_trend_pct
    if trend <= _TAPER_LOAD_DROP_PCT and hard_days >= 1:
        return "taper", "medium", [f"load_trend_{trend:.0f}pct_with_hard_effort"]
    if trend >= _BUILD_LOAD_RISE_PCT:
        return "build", "medium", [f"load_trend_{trend:+.0f}pct_rising"]
    if hard_days >= _PEAK_MIN_HARD_DAYS_7D:
        return "peak", "medium", [f"hard_days_7d_{hard_days}"]

    return "base", "low", ["steady_load_no_strong_signal"]


def _secondary_phase_from_target_date(
    target_date: date | None, *, today: date
) -> DetectedPhase | None:
    if target_date is None or target_date < today:
        return None
    days_out = (target_date - today).days
    if days_out <= _TARGET_DATE_TAPER_DAYS:
        return "taper"
    if days_out <= _TARGET_DATE_PEAK_DAYS:
        return "peak"
    if days_out <= _TARGET_DATE_BUILD_DAYS:
        return "build"
    return "base"


def detect_training_phase(
    logs: list,
    *,
    today: date,
    plan_week_phase: str | None = None,
    target_date: date | None = None,
) -> PhaseDetectionResult | None:
    """Classifie la phase d'entraînement diagnostique depuis le comportement récent
    (flux principal, obligatoire), recoupée avec la phase déclarée par le plan actif si
    disponible, sinon avec la proximité de la date cible (flux secondaire, best-effort).

    `None` si `logs` est vide — rien à diagnostiquer, jamais une phase par défaut
    hallucinée sur zéro donnée.

    `plan_week_phase` : phase de la semaine courante du plan actif (`week.phase`), si un
    plan existe — c'est la vérité la plus fiable disponible pour le flux secondaire, donc
    prioritaire sur la proximité de date cible.
    """
    if not logs:
        return None

    detected_phase, confidence, reasons = _primary_phase(logs, today=today)

    secondary_phase: DetectedPhase | None = None
    if plan_week_phase in _VALID_PHASES:
        secondary_phase = plan_week_phase  # type: ignore[assignment]
    else:
        secondary_phase = _secondary_phase_from_target_date(target_date, today=today)

    streams_agree: bool | None = None
    if secondary_phase is not None:
        streams_agree = detected_phase == secondary_phase
        if streams_agree and confidence == "medium":
            confidence = "high"

    return PhaseDetectionResult(
        detected_phase=detected_phase,
        confidence=confidence,
        reason_codes=reasons,
        secondary_phase=secondary_phase,
        streams_agree=streams_agree,
    )
