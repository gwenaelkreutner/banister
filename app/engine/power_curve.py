"""Power-curve delta / sustainability_profile — porté depuis un script tiers (Section11,
fourni par l'utilisateur), formule et garde-fous vérifiés contre l'API réelle
intervals.icu le 2026-09-21 (`client.get_power_curves()`).

Consomme la réponse de `client.get_power_curves()` telle quelle (Principe IV) — les
watts par ancrage viennent de la source (la MMP intervals.icu la plus élevée sur la
fenêtre), rien n'est recalculé. Seules la comparaison entre deux fenêtres
(`compute_power_curve_delta`) et les modèles de soutenabilité (Coggan, Skiba —
`compute_sustainability_profile`) sont calculés ici, car la source ne les fournit pas.

Zéro appel réseau ici — chaque fonction prend en entrée la réponse JSON déjà récupérée
par l'appelant (même conception que `app/engine/tid.py`/`weekly_snapshot.py`).
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Power-curve delta ─────────────────────────────────────────────────────────

_DELTA_ANCHORS_SECONDS = {"5s": 5, "60s": 60, "300s": 300, "1200s": 1200, "3600s": 3600}
_ROTATION_SHORT = ("5s", "60s")
_ROTATION_LONG = ("1200s", "3600s")
_DELTA_MIN_VALID_ANCHORS = 3


@dataclass(frozen=True)
class AnchorDelta:
    current_watts: float | None
    previous_watts: float | None
    pct_change: float | None


@dataclass(frozen=True)
class PowerCurveDelta:
    anchors: dict[str, AnchorDelta]
    rotation_index: float | None
    note: str | None


def _curve_value_at(curve: dict, duration_s: int, *, values_key: str) -> float | None:
    secs = curve.get("secs") or []
    values = curve.get(values_key) or []
    if duration_s not in secs:
        return None
    idx = secs.index(duration_s)
    if idx >= len(values):
        return None
    val = values[idx]
    return val if (val is not None and val > 0) else None


def compute_power_curve_delta(
    power_curves_response: dict, *, current_window_id: str, previous_window_id: str
) -> PowerCurveDelta:
    """`power_curves_response` : réponse brute de `client.get_power_curves(curve_type=
    "power", windows=[...])`. `current_window_id`/`previous_window_id` : les mêmes
    `"r.<oldest>.<newest>"` que les tuples passés à `windows=`, pour retrouver la bonne
    courbe par id — l'API omet une fenêtre sans activité qualifiante, jamais par
    position de liste (vérifié sur script source).

    Rotation index = moyenne(deltas courts) − moyenne(deltas longs), courts = 5s/60s,
    longs = 1200s/3600s, 300s exclu (transitoire). Positif = gains biaisés sprint,
    négatif = gains biaisés endurance — convention interne, pas de source académique
    pour ce nom précis (inspirée de Section11).
    """
    curves_by_id = {c["id"]: c for c in (power_curves_response.get("list") or []) if "id" in c}
    current_curve = curves_by_id.get(current_window_id)
    previous_curve = curves_by_id.get(previous_window_id)

    if current_curve is None or previous_curve is None:
        missing = [
            name
            for name, curve in (("actuelle", current_curve), ("précédente", previous_curve))
            if curve is None
        ]
        return PowerCurveDelta(
            anchors={}, rotation_index=None,
            note=f"Pas de données puissance sur la fenêtre {' et '.join(missing)}.",
        )

    anchors: dict[str, AnchorDelta] = {}
    for label, duration_s in _DELTA_ANCHORS_SECONDS.items():
        cur_w = _curve_value_at(current_curve, duration_s, values_key="watts")
        prev_w = _curve_value_at(previous_curve, duration_s, values_key="watts")
        pct_change = None
        if cur_w is not None and prev_w is not None and prev_w > 0:
            pct_change = round((cur_w - prev_w) / prev_w * 100, 1)
        anchors[label] = AnchorDelta(
            current_watts=cur_w, previous_watts=prev_w, pct_change=pct_change
        )

    current_valid = sum(1 for a in anchors.values() if a.current_watts is not None)
    previous_valid = sum(1 for a in anchors.values() if a.previous_watts is not None)
    if current_valid < _DELTA_MIN_VALID_ANCHORS or previous_valid < _DELTA_MIN_VALID_ANCHORS:
        return PowerCurveDelta(
            anchors=anchors, rotation_index=None,
            note=(
                f"Trop peu d'ancrages valides (actuelle : {current_valid}, "
                f"précédente : {previous_valid}, {_DELTA_MIN_VALID_ANCHORS} minimum)."
            ),
        )

    short_changes = [anchors[a].pct_change for a in _ROTATION_SHORT]
    long_changes = [anchors[a].pct_change for a in _ROTATION_LONG]
    rotation_index = None
    if all(v is not None for v in [*short_changes, *long_changes]):
        rotation_index = round(
            sum(short_changes) / len(short_changes) - sum(long_changes) / len(long_changes), 1
        )

    return PowerCurveDelta(anchors=anchors, rotation_index=rotation_index, note=None)


# ── Sustainability profile (cyclisme uniquement) ─────────────────────────────

# Facteurs de durée Coggan — milieux des fourchettes publiées.
# Source : Allen & Coggan, "Training and Racing with a Power Meter" (3e éd.) — puissance
# soutenable en fraction de la FTP par durée.
COGGAN_DURATION_FACTORS: dict[int, float] = {
    300: 1.06,   # 5min  : ~106% FTP (fourchette 100-112%)
    600: 0.97,   # 10min : ~97% FTP (fourchette 94-100%)
    1200: 0.93,  # 20min : ~93% FTP (fourchette 91-95%)
    1800: 0.90,  # 30min : ~90% FTP (fourchette 88-93%)
    3600: 0.86,  # 60min : ~86% FTP (fourchette 83-90%)
    5400: 0.82,  # 90min : ~82% FTP (fourchette 78-85%)
    7200: 0.78,  # 2h    : ~78% FTP (fourchette 75-82%)
}

_SUSTAINABILITY_ANCHORS_SECONDS = list(COGGAN_DURATION_FACTORS.keys())
_SUSTAINABILITY_MIN_OBSERVED_ANCHORS = 2


@dataclass(frozen=True)
class SustainabilityAnchor:
    duration_s: int
    actual_watts: float | None
    actual_wpkg: float | None
    coggan_watts: int | None
    cp_model_watts: int | None  # Skiba CP/W', CP approximé par la FTP
    model_divergence_pct: float | None  # (actual - cp_model) / cp_model * 100


@dataclass(frozen=True)
class SustainabilityProfile:
    anchors: dict[str, SustainabilityAnchor]
    coverage_ratio: float
    ftp_used: float | None
    w_prime_used: float | None
    note: str | None


def compute_sustainability_profile(
    power_curves_responses: list[dict],
    *,
    window_id: str,
    ftp: float | None,
    w_prime: float | None,
    weight_kg: float | None = None,
) -> SustainabilityProfile:
    """`power_curves_responses` : une réponse `client.get_power_curves()` par type
    d'activité cyclisme déjà récupérée par l'appelant (ex. Ride + VirtualRide, une
    fenêtre unique de 42j) — fusionnées ici en prenant le meilleur watt par ancrage
    entre les types (même logique que Section11 : indoor/outdoor mergés, pas un choix
    arbitraire d'un seul type). `window_id` : le `"r.<oldest>.<newest>"` de la fenêtre
    unique demandée.

    Modèle Coggan (`COGGAN_DURATION_FACTORS`) et modèle CP/W' de Skiba (`P = CP + W'/t`,
    CP approximé par la FTP — Skiba 2012, *"Modeling the expenditure and reconstitution
    of work capacity above critical power"*) comparés à la puissance réellement tenue,
    aux ancrages 300s-7200s. Aucun ajustement/fitting — formules fermées uniquement.
    """
    best_watts_by_anchor: dict[int, float] = {}
    for response in power_curves_responses:
        curves_by_id = {c["id"]: c for c in (response.get("list") or []) if "id" in c}
        curve = curves_by_id.get(window_id)
        if curve is None:
            continue
        for duration_s in _SUSTAINABILITY_ANCHORS_SECONDS:
            val = _curve_value_at(curve, duration_s, values_key="watts")
            if val is None:
                continue
            if duration_s not in best_watts_by_anchor or val > best_watts_by_anchor[duration_s]:
                best_watts_by_anchor[duration_s] = val

    anchors: dict[str, SustainabilityAnchor] = {}
    for duration_s in _SUSTAINABILITY_ANCHORS_SECONDS:
        label = f"{duration_s}s"
        actual_watts = best_watts_by_anchor.get(duration_s)
        actual_wpkg = (
            round(actual_watts / weight_kg, 2)
            if (actual_watts is not None and weight_kg and weight_kg > 0)
            else None
        )
        coggan_watts = (
            round(ftp * COGGAN_DURATION_FACTORS[duration_s]) if ftp else None
        )
        cp_model_watts = (
            round(ftp + w_prime / duration_s) if (ftp and w_prime) else None
        )
        model_divergence_pct = None
        if actual_watts is not None and cp_model_watts is not None and cp_model_watts > 0:
            model_divergence_pct = round(
                (actual_watts - cp_model_watts) / cp_model_watts * 100, 1
            )
        anchors[label] = SustainabilityAnchor(
            duration_s=duration_s,
            actual_watts=actual_watts,
            actual_wpkg=actual_wpkg,
            coggan_watts=coggan_watts,
            cp_model_watts=cp_model_watts,
            model_divergence_pct=model_divergence_pct,
        )

    observed = sum(1 for a in anchors.values() if a.actual_watts is not None)
    total = len(anchors)
    coverage_ratio = round(observed / total, 2) if total else 0.0

    if observed < _SUSTAINABILITY_MIN_OBSERVED_ANCHORS:
        note = (
            f"Trop peu d'ancrages observés ({observed}, "
            f"{_SUSTAINABILITY_MIN_OBSERVED_ANCHORS} minimum)."
        )
        return SustainabilityProfile(
            anchors=anchors, coverage_ratio=coverage_ratio,
            ftp_used=ftp, w_prime_used=w_prime, note=note,
        )

    return SustainabilityProfile(
        anchors=anchors, coverage_ratio=coverage_ratio,
        ftp_used=ftp, w_prime_used=w_prime, note=None,
    )
