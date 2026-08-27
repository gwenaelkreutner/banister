"""
Estimation du TSS (Training Stress Score) par séance planifiée (plan_builder) et
détection d'anomalie fatigue depuis le RPE déclaré (session_log).

TSS/HRSS par activité réelle ne sont plus calculés ici — consommés depuis
intervals.icu (spec 002 FR-015/016/017). `calc_tss`/`calc_hrss` ont été supprimés
(spec 002 T018/T019) : leur dernier appelant réel, l'import historique Strava, a
disparu en Phase 7.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.schemas import RepeatGroup, Step, Zone  # noqa: F401 — Zone réexporté pour rétrocompat

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

# Correspondance emoji RPE → entier 1-10
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


def estimate_structured_session_tss(
    steps: list[Step | RepeatGroup],
    coaching_mode: str,
    ftp: int | None = None,
) -> float:
    """TSS for a structured session (spec 004 FR-006) — sums `estimate_session_tss()`
    per step, with a RepeatGroup's steps counted `repeat` times.

    Accepts `ftp` for signature symmetry with `estimate_session_tss()`, but it does not
    change the result in power mode: NP is derived from `ftp × zone_if`, and the TSS
    formula divides by `ftp` again, so `ftp` cancels out of `estimate_session_tss_power()`
    algebraically — only the per-zone intensity factor determines the answer. `coaching_mode`
    is what actually selects the formula (power's IF² curve vs HR's empirical TSS/hour table).
    """
    total = 0.0
    for item in steps:
        if isinstance(item, RepeatGroup):
            for s in item.steps:
                total += item.repeat * estimate_session_tss(
                    s.zone_code, s.duration_minutes, coaching_mode, ftp
                )
        else:
            total += estimate_session_tss(item.zone_code, item.duration_minutes, coaching_mode, ftp)
    return round(total, 1)


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
