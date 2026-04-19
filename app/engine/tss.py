"""
Estimation du TSS (Training Stress Score) par séance.

TSS = (durée_s × NP × IF) / (FTP × 3600) × 100
  IF = NP / FTP

En mode HR (sans capteur de puissance) — HRSS via TRIMP de Banister :
  HRR_i = (HR_i − HR_rest) / (HR_max − HR_rest)
  stress_i = dt_i × HRR_i × 0.64 × exp(k × HRR_i)   (k = 1.92 H / 1.67 F)
  TRIMP    = Σ stress_i
  HRSS     = TRIMP / TRIMP_1h_LTHR × 100

Calcul continu sur la série temporelle secondaire (streams Strava).
NumPy utilisé pour les performances sur sorties > 6h (≥ 21 600 points).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.engine.schemas import Zone  # noqa: F401 — réexporté pour rétrocompat

# ── Constantes ─────────────────────────────────────────────────────────────

# Facteur IF approximatif par zone (NP / FTP)
ZONE_IF = {
    "Z1": 0.48,
    "Z2": 0.65,
    "Z3": 0.81,
    "Z4": 0.91,
    "Z5": 1.00,
    "Z6": 1.20,
}

# Facteur TSS/heure en mode HR empirique (sans streams, fallback plan builder)
ZONE_TSS_PER_HOUR_HR = {
    "Z1": 20,
    "Z2": 45,
    "Z3": 65,
    "Z4": 85,
    "Z5": 100,
    "Z6": 120,
}

# Coefficients TRIMP de Banister — pondération exponentielle sexe-spécifique
_K_TRIMP: dict[str, float] = {"M": 1.92, "F": 1.67}
_K_DEFAULT = 1.92  # défaut si sexe inconnu

# Correspondance emoji RPE → entier 1-10 (cohérent avec tss_from_rpe dans atl_ctl.py)
RPE_EMOJI_INT_MAP: dict[str, int] = {
    "easy": 3,
    "normal": 5,
    "hard": 8,
}


# ── Résultats HRSS ─────────────────────────────────────────────────────────


@dataclass
class FatigueAnomaly:
    """
    Alerte levée quand le RPE déclaré dépasse significativement la charge
    cardiaque théorique (écart ≥ 3 points sur 10).

    Indique une possible fatigue résiduelle, un état de forme dégradé ou
    une inadéquation entre effort perçu et réponse physiologique.
    """

    rpe_declared: int
    rpe_cardiac_estimate: float  # HRR moyen pondéré × 10
    rpe_delta: float
    rpe_factor: float  # multiplicateur appliqué au HRSS (1.10 – 1.20)
    message: str


@dataclass
class HRSSResult:
    """Résultat du calcul HRSS depuis les streams HR d'une activité."""

    hrss: float
    trimp: float
    tss_method: str  # toujours "hrss"
    rpe_factor: float
    fatigue_anomaly: FatigueAnomaly | None


# ── Calcul principal HRSS ──────────────────────────────────────────────────


def calc_hrss(
    hr_series: list[float],
    time_series: list[int],
    hr_rest: int,
    hr_max: int,
    threshold_hr: int,
    sex: str = "M",
    user_rpe: int | None = None,
) -> HRSSResult:
    """
    Calcule le HRSS (Heart Rate Stress Score) par intégration continue
    du TRIMP de Banister sur la série temporelle de l'activité.

    Paramètres
    ----------
    hr_series    : fréquences cardiaques instantanées (bpm), par seconde
    time_series  : timestamps en secondes depuis le début
    hr_rest      : FC de repos (bpm)
    hr_max       : FC maximale (bpm)
    threshold_hr : LTHR — FC au seuil lactique (bpm)
    sex          : "M" (k = 1.92) ou "F" (k = 1.67)
    user_rpe     : RPE déclaré (smiley → entier 1-10), optionnel

    Retour
    ------
    HRSSResult avec hrss, trimp, rpe_factor, et fatigue_anomaly éventuelle.
    """
    _EMPTY = HRSSResult(
        hrss=0.0, trimp=0.0, tss_method="hrss", rpe_factor=1.0, fatigue_anomaly=None
    )

    n = min(len(hr_series), len(time_series))
    if n < 2:
        return _EMPTY

    hr_range = float(hr_max - hr_rest)
    if hr_range <= 0:
        return _EMPTY

    # ── Vectorisation numpy ───────────────────────────────────────────────
    hr = np.array(hr_series[:n], dtype=np.float64)
    t = np.array(time_series[:n], dtype=np.float64)

    # Valeurs absentes / hors plage → hr_rest (HRR = 0, contribution nulle)
    hr_clean = np.where(np.isfinite(hr) & (hr > 0), hr, float(hr_rest))
    hr_clean = np.clip(hr_clean, float(hr_rest), float(hr_max))

    hrr = (hr_clean - hr_rest) / hr_range  # [0, 1]

    k = _K_TRIMP.get(sex.upper(), _K_DEFAULT)

    # dt[i] = durée de l'intervalle i→i+1 ; valeur HR = échantillon de fin
    dt = np.maximum(np.diff(t), 0.0)  # shape (n-1,)
    hrr_end = hrr[1:]                  # shape (n-1,)

    # Stress instantané (formule TRIMP de Banister)
    stress = dt * hrr_end * 0.64 * np.exp(k * hrr_end)
    trimp = float(np.sum(stress))

    # ── Normalisation HRSS ────────────────────────────────────────────────
    hrr_lthr = float(np.clip((threshold_hr - hr_rest) / hr_range, 0.0, 1.0))
    trimp_1h_lthr = 3600.0 * hrr_lthr * 0.64 * np.exp(k * hrr_lthr)

    if trimp_1h_lthr <= 0:
        return _EMPTY

    hrss = min((trimp / trimp_1h_lthr) * 100.0, 400.0)

    # ── Pondération RPE ───────────────────────────────────────────────────
    rpe_factor = 1.0
    fatigue_anomaly: FatigueAnomaly | None = None

    if user_rpe is not None:
        total_time = float(np.sum(dt))
        if total_time > 0:
            # HRR moyen pondéré par le temps (plus juste sur sorties à rythme variable)
            weighted_hrr = float(np.sum(hrr_end * dt) / total_time)
        else:
            weighted_hrr = float(np.mean(hrr_end))

        cardiac_rpe_estimate = round(weighted_hrr * 10.0, 1)
        rpe_delta = float(user_rpe) - cardiac_rpe_estimate

        if rpe_delta >= 3.0:
            # Écart significatif : le corps souffre bien au-delà de ce que la FC indique
            rpe_factor = round(min(1.1 + (rpe_delta - 3.0) * 0.05, 1.2), 3)
            fatigue_anomaly = FatigueAnomaly(
                rpe_declared=int(user_rpe),
                rpe_cardiac_estimate=cardiac_rpe_estimate,
                rpe_delta=round(rpe_delta, 1),
                rpe_factor=rpe_factor,
                message=(
                    f"RPE déclaré ({user_rpe}/10) nettement supérieur à la charge cardiaque "
                    f"estimée ({cardiac_rpe_estimate}/10). Possible fatigue résiduelle ou état "
                    "de forme dégradé — surveiller la récupération."
                ),
            )
            hrss = min(hrss * rpe_factor, 400.0)

    return HRSSResult(
        hrss=round(hrss, 1),
        trimp=round(trimp, 2),
        tss_method="hrss",
        rpe_factor=rpe_factor,
        fatigue_anomaly=fatigue_anomaly,
    )


# ── Détection anomalie RPE sans streams (version scalaire) ────────────────


def detect_fatigue_anomaly_scalar(
    avg_hr: float,
    hr_rest: int,
    hr_max: int,
    user_rpe: int,
    sex: str = "M",
) -> FatigueAnomaly | None:
    """
    Détecte une anomalie fatigue depuis la FC moyenne (pas de streams requis).

    Utilisé au moment de la soumission du RPE, quand les streams ne sont
    plus disponibles. Moins précis que calc_hrss() mais suffisant pour la
    détection binaire (delta ≥ 3 sur 10).

    Paramètres
    ----------
    avg_hr   : FC moyenne de la séance (bpm) — stockée dans session_log
    hr_rest  : FC de repos (bpm)
    hr_max   : FC maximale (bpm)
    user_rpe : RPE déclaré converti en entier 1-10 (via RPE_EMOJI_INT_MAP)
    sex      : "M" | "F"

    Retour
    ------
    FatigueAnomaly si écart ≥ 3 pts, None sinon.
    """
    hr_range = float(hr_max - hr_rest)
    if hr_range <= 0 or avg_hr <= 0:
        return None

    avg_hrr = max(0.0, min(1.0, (avg_hr - hr_rest) / hr_range))
    cardiac_rpe_estimate = round(avg_hrr * 10.0, 1)
    rpe_delta = float(user_rpe) - cardiac_rpe_estimate

    if rpe_delta < 3.0:
        return None

    rpe_factor = round(min(1.1 + (rpe_delta - 3.0) * 0.05, 1.2), 3)
    return FatigueAnomaly(
        rpe_declared=int(user_rpe),
        rpe_cardiac_estimate=cardiac_rpe_estimate,
        rpe_delta=round(rpe_delta, 1),
        rpe_factor=rpe_factor,
        message=(
            f"RPE déclaré ({user_rpe}/10) nettement supérieur à la charge cardiaque "
            f"estimée ({cardiac_rpe_estimate}/10). Possible fatigue résiduelle ou état "
            "de forme dégradé — surveiller la récupération."
        ),
    )


# ── Fonctions de plan builder (estimation par zones) ──────────────────────


def estimate_session_tss_power(
    zone_code: str,
    duration_minutes: int,
    ftp: int,
) -> float:
    """Estimation TSS en mode power depuis FTP."""
    if_factor = ZONE_IF.get(zone_code, 0.65)
    np_approx = ftp * if_factor
    duration_sec = duration_minutes * 60
    tss = (duration_sec * np_approx * if_factor) / (ftp * 3600) * 100
    return round(tss, 1)


def estimate_session_tss_hr(zone_code: str, duration_minutes: int) -> float:
    """Estimation TSS en mode HR (sans capteur de puissance)."""
    tss_per_hour = ZONE_TSS_PER_HOUR_HR.get(zone_code, 45)
    tss = tss_per_hour * (duration_minutes / 60)
    return round(tss, 1)


def estimate_session_tss(
    zone_code: str,
    duration_minutes: int,
    coaching_mode: str,
    ftp: int | None = None,
) -> float:
    """Calcul TSS selon le mode de coaching."""
    if coaching_mode == "power" and ftp is not None:
        return estimate_session_tss_power(zone_code, duration_minutes, ftp)
    return estimate_session_tss_hr(zone_code, duration_minutes)


def weekly_tss_from_sessions(
    sessions: list[tuple[str, int]],  # [(zone_code, duration_minutes), ...]
    coaching_mode: str,
    ftp: int | None = None,
) -> float:
    """TSS total d'une semaine depuis la liste des séances."""
    return sum(
        estimate_session_tss(zone, duration, coaching_mode, ftp)
        for zone, duration in sessions
    )


def tss_from_weekly_hours(hours: float) -> float:
    """
    Estimation TSS hebdomadaire depuis le volume en heures.
    Hypothèse : Z2 dominant (40 TSS/h).
    """
    return round(hours * 40, 1)


# ── Calcul TSS legacy (import historique, fallback sans streams) ───────────


def calc_tss(
    duration_s: int,
    normalized_w: float | None,
    avg_hr: float | None,
    ftp: int | None,
    threshold_hr: int | None,
    suffer_score: int | None,
) -> tuple[float, str]:
    """
    Calcul TSS par ordre de priorité : power > hr > estimation.

    Retourne (tss, méthode) — méthode ∈ {"power", "hr", "estimation"}.

    Utilisé pour l'import historique (avg_hr scalaire, pas de streams).
    Pour le calcul haute fidélité sur streams temps-réel, utiliser calc_hrss().
    """
    if normalized_w and ftp and ftp > 0 and duration_s > 0:
        intensity_factor = normalized_w / ftp
        tss = (duration_s * normalized_w * intensity_factor) / (ftp * 3600) * 100
        return min(tss, 400), "power"

    if avg_hr and threshold_hr and threshold_hr > 0 and duration_s > 0:
        duration_h = duration_s / 3600
        hr_ratio = avg_hr / threshold_hr
        tss = duration_h * hr_ratio * hr_ratio * 100
        return min(tss, 400), "hr"

    if suffer_score and suffer_score > 0:
        return min(suffer_score * 0.7, 400), "estimation"

    if duration_s > 0:
        return min((duration_s / 3600) * 50, 400), "estimation"

    return 0.0, "estimation"
