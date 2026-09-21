"""TID (Training Intensity Distribution) / indice de polarisation.

Calculs déterministes, zéro appel LLM (Principe I) — même esprit que
`app/engine/weekly_snapshot.py`. Agrège `SessionLog.time_in_zones_s` (JSON persisté par
séance, déjà rempli à l'ingestion via `mapper.py::_time_in_zones` — voir `app/db/models/
session_log.py`) sur une fenêtre glissante, sans nouvel appel API ni nouvelle colonne.

⚠️ Scope volontairement limité aux `SessionLog` (pas `Activity`, qui n'a aucune colonne de
temps par zone) — donc uniquement les séances effectivement loguées via le poller/chat, pas
tout l'historique brut importé. Une semaine sans aucune séance loguée renvoie `None`, jamais
un TID halluciné sur zéro donnée.

Trois zones (Seiler, 2010 — modèle descriptif à 3 zones, "What is best practice for
training intensity and duration distribution in endurance athletes?", Int J Sports
Physiol Perform) mappées depuis les 7 zones de puissance intervals.icu, même regroupement
que `mapper.py::_detect_session_type` (z1_z2/z3_z4/z4_plus) mais en partition propre à 3
buckets qui somment à 100% :
  - zone1 (faible, sous LT1)      = Z1 + Z2
  - zone2 (modérée, LT1-LT2)      = Z3 + Z4
  - zone3 (élevée, au-dessus LT2) = Z5 + Z6 + Z7
"SS" (sweet spot, sous-compartiment de conformité qui chevauche Z3/Z4) est exclu, même
précédent que `mapper.py::_dominant_zone`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import log10
from typing import Literal

from app.engine.guardrail_thresholds import (
    POLARIZATION_INDEX_THRESHOLD,
    TID_BASE_ZONE1_MIN_PCT,
    TID_HIGH_INTENSITY_ZONE3_MIN_PCT,
    TID_THRESHOLD_ZONE2_MIN_PCT,
)

Classification = Literal[
    "polarized", "pyramidal", "threshold", "high_intensity", "base", "unclassified"
]


@dataclass
class TIDResult:
    zone1_pct: float
    zone2_pct: float
    zone3_pct: float
    total_seconds: float
    sessions_counted: int
    polarization_index: float | None
    classification: Classification


def _bucket_seconds(time_in_zones_s: dict) -> tuple[float, float, float]:
    zone1 = time_in_zones_s.get("Z1", 0) + time_in_zones_s.get("Z2", 0)
    zone2 = time_in_zones_s.get("Z3", 0) + time_in_zones_s.get("Z4", 0)
    zone3 = sum(time_in_zones_s.get(z, 0) for z in ("Z5", "Z6", "Z7"))
    return zone1, zone2, zone3


def _polarization_index(zone1_pct: float, zone2_pct: float, zone3_pct: float) -> float | None:
    """Treff et al. 2019, "The Polarization-Index: A Simple Calculation to Distinguish
    Polarized From Other Training Intensity Distributions" (Front Physiol) :
    log10((Z1/Z2) x Z3 x 100), où Z1/Z2/Z3 sont des FRACTIONS (0-1) dans la formule
    publiée. Ici zone1_pct/zone2_pct/zone3_pct sont déjà en pourcentage (0-100) — donc
    zone3_frac x 100 == zone3_pct directement, et le `x 100` de la formule d'origine ne
    doit PAS être réappliqué (bug trouvé et corrigé en écrivant les tests, 2026-09-21 :
    un `x 100` en trop gonflait l'indice de ~2 ordres de grandeur et rendait "polarized"
    quasi inévitable, y compris sur des semaines clairement dominées par la zone 2).
    `None` si une zone est à 0% — hors domaine du log, jamais un chiffre halluciné."""
    if zone1_pct <= 0 or zone2_pct <= 0 or zone3_pct <= 0:
        return None
    return round(log10((zone1_pct / zone2_pct) * zone3_pct), 2)


def _classify(
    zone1_pct: float, zone2_pct: float, zone3_pct: float, polarization_index: float | None
) -> Classification:
    """Seuils documentés (source ou adaptation explicite) dans
    `app/engine/guardrail_thresholds.py`, bloc "TID / Polarisation"."""
    if polarization_index is not None and polarization_index >= POLARIZATION_INDEX_THRESHOLD:
        return "polarized"
    if zone1_pct >= TID_BASE_ZONE1_MIN_PCT:
        return "base"
    if zone2_pct >= TID_THRESHOLD_ZONE2_MIN_PCT and zone2_pct >= zone3_pct:
        return "threshold"
    if zone3_pct >= TID_HIGH_INTENSITY_ZONE3_MIN_PCT and zone3_pct >= zone2_pct:
        return "high_intensity"
    if zone1_pct > zone2_pct > zone3_pct:
        return "pyramidal"
    return "unclassified"


def compute_tid(logs: list, *, today: date, window_days: int) -> TIDResult | None:
    """Agrège `time_in_zones_s` des `SessionLog` dans `[today - window_days + 1, today]`.

    `None` si aucune séance avec données de zone dans la fenêtre — jamais un résultat à
    0%/0%/0% qui laisserait croire à une semaine réellement mesurée à zéro intensité.
    """
    cutoff = today - timedelta(days=window_days - 1)
    zone1 = zone2 = zone3 = 0.0
    sessions_counted = 0

    for log in logs:
        logged_date = getattr(log, "logged_date", None)
        time_in_zones_s = getattr(log, "time_in_zones_s", None)
        if logged_date is None or not time_in_zones_s:
            continue
        if not (cutoff <= logged_date <= today):
            continue
        z1, z2, z3 = _bucket_seconds(time_in_zones_s)
        if z1 + z2 + z3 <= 0:
            continue
        zone1 += z1
        zone2 += z2
        zone3 += z3
        sessions_counted += 1

    total = zone1 + zone2 + zone3
    if total <= 0:
        return None

    zone1_pct = round(zone1 / total * 100, 1)
    zone2_pct = round(zone2 / total * 100, 1)
    zone3_pct = round(zone3 / total * 100, 1)
    polarization_index = _polarization_index(zone1_pct, zone2_pct, zone3_pct)
    classification = _classify(zone1_pct, zone2_pct, zone3_pct, polarization_index)

    return TIDResult(
        zone1_pct=zone1_pct,
        zone2_pct=zone2_pct,
        zone3_pct=zone3_pct,
        total_seconds=total,
        sessions_counted=sessions_counted,
        polarization_index=polarization_index,
        classification=classification,
    )
