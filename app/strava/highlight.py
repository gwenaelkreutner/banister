"""
Moteur de sélection du "highlight" post-séance.

Implémente deux mécaniques :
  1. select_highlight()       — choisit la métrique la plus remarquable de la séance
                                avec un tirage pondéré (même perf → angle différent)
  2. detect_personal_records() — détecte un record personnel in-memory (pas de requête DB)

Ces deux fonctions alimentent la notification Telegram en 3 temps (Messages A, B, C)
et le prompt LLM post-RPE.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.session_log import SessionLog
    from app.engine.atl_ctl import FitnessMetrics
    from app.engine.weekly_snapshot import WeeklySnapshot
    from app.strava.analysis_models import AnalyzedSession


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class HighlightResult:
    category: str   # "CONSISTENCY_KING" | "CARDIAC_STORY" | "ZONE_DISCIPLINE" |
                    # "POWER_PEAK" | "TSB_SIGNAL" | "VOLUME_CONTEXT"
    teaser: str     # Ligne 2 du Message A (accroche, sans données brutes)
    hero_metric: str  # Ligne héros dans le Message B (métrique principale + valeur)
    hero_detail: str  # Explication courte sur la ligne héros (1 ligne)


@dataclass
class PersonalRecord:
    metric: str          # "tss" | "if" | "consistency" | "zones_score"
    value: float
    previous_best: float
    sessions_compared: int
    label_fr: str        # "Ton meilleur TSS sur intervalles !"


# ---------------------------------------------------------------------------
# Session type icons (doit rester synchronisé avec webhook.py)
# ---------------------------------------------------------------------------

_SESSION_ICON: dict[str, str] = {
    "intervals": "⚡",
    "endurance": "🟢",
    "recovery":  "🫶",
    "tempo":     "🟠",
    "long_ride": "🚴",
    "race":      "🏁",
    "unknown":   "🚴",
}

_SESSION_LABEL: dict[str, str] = {
    "intervals": "INTERVALLES",
    "endurance": "ENDURANCE",
    "recovery":  "RÉCUPÉRATION ACTIVE",
    "tempo":     "TEMPO",
    "long_ride": "SORTIE LONGUE",
    "race":      "COURSE",
    "unknown":   "SORTIE",
}


# ---------------------------------------------------------------------------
# Highlight categories
# ---------------------------------------------------------------------------

def _pct_fmt(val: float) -> str:
    """Formate un drift ou un score avec signe."""
    return f"{val:+.0f}%"


def _tss_fmt(tss: float | None) -> str:
    """Le TSS peut être None — pas de charge calculable côté source pour cette activité
    (spec 002 research R9c, ~15% des activités réelles). '—' plutôt qu'un crash sur None."""
    return f"{tss:.0f}" if tss is not None else "—"


def _select_candidates(
    analyzed: "AnalyzedSession",
    fitness: "FitnessMetrics",
    weekly: "WeeklySnapshot",
) -> list[tuple[HighlightResult, float]]:
    """Retourne la liste des catégories éligibles avec leurs poids."""
    candidates: list[tuple[HighlightResult, float]] = []

    ci = analyzed.intervals_consistency_index  # 0–1
    drift = analyzed.cardiac_drift_index       # signed float (ex: 0.14 = +14%)
    zones_score = analyzed.respect_zones_score # 0–100
    if_ = analyzed.intensity_factor
    tsb = fitness.tsb
    tss = analyzed.tss
    tss_6w_daily = weekly.tss_6w_avg / 7 if weekly.tss_6w_avg else None

    # CONSISTENCY_KING — intervalles chirurgicaux
    if ci is not None and ci >= 0.88:
        pct = int(ci * 100)
        candidates.append((
            HighlightResult(
                category="CONSISTENCY_KING",
                teaser="Tes intervalles étaient chirurgicaux 🎯",
                hero_metric=f"Consistance intervalles : {pct}%",
                hero_detail="Régularité de puissance d'intervalle en intervalle",
            ),
            1.4,
        ))

    # CARDIAC_STORY — dérive cardiaque notable
    if drift is not None and abs(drift) >= 0.08:
        drift_pct = drift * 100
        if drift > 0:
            # Dérive positive = cardio monte → signal de fatigue
            candidates.append((
                HighlightResult(
                    category="CARDIAC_STORY",
                    teaser="Ton cœur a raconté une histoire ce matin... 📈",
                    hero_metric=f"Drift cardiaque : {drift_pct:+.0f}% ⚠️",
                    hero_detail="Cardio monté en 2e moitié — signal de fatigue ou chaleur",
                ),
                1.2,
            ))
        else:
            # Dérive négative = cardio stable ou baisse → bonne économie
            candidates.append((
                HighlightResult(
                    category="CARDIAC_STORY",
                    teaser="Signal de forme : ton cardio a tenu l'effort ✅",
                    hero_metric=f"Drift cardiaque : {drift_pct:+.0f}% ✅",
                    hero_detail="Efficacité cardiaque maintenue du début à la fin",
                ),
                1.2,
            ))

    # ZONE_DISCIPLINE — exécution des zones remarquable (haute ou basse)
    if zones_score is not None and (zones_score >= 90 or zones_score <= 60):
        if zones_score >= 90:
            candidates.append((
                HighlightResult(
                    category="ZONE_DISCIPLINE",
                    teaser="Exécution quasi-parfaite des zones 🎯",
                    hero_metric=f"Respect des zones : {zones_score:.0f}/100 ✅",
                    hero_detail="Discipline d'intensité proche de la perfection",
                ),
                1.0,
            ))
        else:
            candidates.append((
                HighlightResult(
                    category="ZONE_DISCIPLINE",
                    teaser="Séance hors plan — voici ce que ça dit de toi...",
                    hero_metric=f"Respect des zones : {zones_score:.0f}/100 ⚠️",
                    hero_detail="Intensité significativement différente du plan",
                ),
                1.0,
            ))

    # POWER_PEAK — effort quasi-maximal
    if if_ is not None and if_ >= 0.95:
        candidates.append((
            HighlightResult(
                category="POWER_PEAK",
                teaser=f"IF {if_:.2f} — tu es allé chercher le fond 🔥",
                hero_metric=f"Intensité : IF {if_:.2f} 🔥",
                hero_detail="Effort quasi-maximal — proche de ton seuil physiologique",
            ),
            1.3,
        ))

    # TSB_SIGNAL — forme de pointe ou surmenage
    if tsb >= 10:
        candidates.append((
            HighlightResult(
                category="TSB_SIGNAL",
                teaser=f"📈 Aujourd'hui, tu roulais avec des ailes (TSB {tsb:+.0f})",
                hero_metric=f"Forme de pointe : TSB {tsb:+.0f} ✨",
                hero_detail="Pic de fraîcheur — tu es dans ta meilleure fenêtre",
            ),
            0.9,
        ))
    elif tsb <= -25:
        candidates.append((
            HighlightResult(
                category="TSB_SIGNAL",
                teaser="⚠️ Signal de fatigue dans les données...",
                hero_metric=f"Fatigue accumulée : TSB {tsb:+.0f} 🔴",
                hero_detail="Zone de surmenage — récupération prioritaire",
            ),
            0.9,
        ))

    # VOLUME_CONTEXT — fallback toujours valide
    # tss peut être None (source sans charge calculable pour cette activité — spec 002
    # research R9c) : dans ce cas on tombe directement sur le fallback absolu ci-dessous.
    if tss_6w_daily and tss_6w_daily > 0 and tss is not None:
        ratio = int(tss / tss_6w_daily * 100)
        candidates.append((
            HighlightResult(
                category="VOLUME_CONTEXT",
                teaser=f"Cette séance représente {ratio}% de ta charge journalière habituelle...",
                # hero_metric sans "TSS X" pour éviter la duplication avec l'en-tête du Message B
                hero_metric=f"{ratio}% de ta charge habituelle",
                hero_detail=f"Moyenne 6 semaines : {tss_6w_daily:.0f} TSS/jour · Aujourd'hui : {tss:.0f}",
            ),
            0.8,
        ))
    else:
        # Fallback absolu — pas d'historique 6 semaines
        # On met en avant la zone dominante si dispo, sinon la durée brute
        if analyzed.dominant_zone and analyzed.time_in_zones_s:
            dz = analyzed.dominant_zone
            dz_s = analyzed.time_in_zones_s.get(dz, 0)
            total_s = sum(analyzed.time_in_zones_s.values()) or 1
            dz_pct = int(dz_s / total_s * 100)
            candidates.append((
                HighlightResult(
                    category="VOLUME_CONTEXT",
                    teaser="Voyons ce que cette sortie révèle... 👀",
                    hero_metric=f"{dz_pct}% du temps en {dz}",
                    hero_detail="Répartition des efforts analysée",
                ),
                0.8,
            ))
        else:
            candidates.append((
                HighlightResult(
                    category="VOLUME_CONTEXT",
                    teaser="Voyons ce que cette sortie révèle... 👀",
                    hero_metric=f"{(analyzed.moving_time_s or analyzed.duration_s) // 60} min d'effort enregistrés",
                    hero_detail="",
                ),
                0.8,
            ))

    return candidates


def select_highlight(
    analyzed: "AnalyzedSession",
    fitness: "FitnessMetrics",
    weekly: "WeeklySnapshot",
) -> HighlightResult:
    """
    Sélectionne la métrique la plus remarquable de la séance.
    Tirage pondéré : même performance → angle différent à chaque fois (Variable Reward).
    """
    candidates = _select_candidates(analyzed, fitness, weekly)
    results = [c[0] for c in candidates]
    weights = [c[1] for c in candidates]
    return random.choices(results, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Personal records detection (in-memory, pas de requête DB)
# ---------------------------------------------------------------------------

def detect_personal_records(
    all_logs: list["SessionLog"],
    analyzed: "AnalyzedSession",
    current_log_id: object,
) -> PersonalRecord | None:
    """
    Détecte si la séance courante établit un record personnel.

    Reçoit all_logs déjà chargé (évite une requête DB supplémentaire).
    Filtre par session_type_real identique à la séance courante.
    Compare sur : tss_actual, intensity_factor, intervals_consistency_index, respect_zones_score.

    Retourne le record le plus impressionnant, ou None si aucun.
    """
    session_type = analyzed.session_type_real
    if session_type == "unknown":
        return None

    # Logs de référence : même type, déjà terminés, pas le log courant
    pool = [
        lg for lg in all_logs
        if lg.status == "done"
        and lg.session_type_real == session_type
        and lg.id != current_log_id
        and lg.tss_actual is not None
    ]
    if len(pool) < 3:
        # Pas assez d'historique pour déclarer un record significatif
        return None

    n = len(pool)

    # Candidats records, par ordre de "wow factor"
    checks: list[PersonalRecord | None] = [
        _check_pr_consistency(analyzed, pool, n),
        _check_pr_if(analyzed, pool, n),
        _check_pr_zones_score(analyzed, pool, n),
        _check_pr_tss(analyzed, pool, n),
    ]
    # Retourner le premier record trouvé (ordre = priorité)
    for pr in checks:
        if pr is not None:
            return pr
    return None


def _check_pr_consistency(analyzed: "AnalyzedSession", pool: list, n: int) -> PersonalRecord | None:
    if analyzed.intervals_consistency_index is None:
        return None
    values = [lg.intervals_consistency_index for lg in pool if lg.intervals_consistency_index is not None]
    if len(values) < 3:
        return None
    best = max(values)
    current = analyzed.intervals_consistency_index
    if current > best:
        return PersonalRecord(
            metric="consistency",
            value=round(current * 100, 1),
            previous_best=round(best * 100, 1),
            sessions_compared=len(values),
            label_fr=f"Meilleure consistance d'intervalles ({int(current * 100)}%)",
        )
    return None


def _check_pr_if(analyzed: "AnalyzedSession", pool: list, n: int) -> PersonalRecord | None:
    if analyzed.intensity_factor is None:
        return None
    values = [lg.intensity_factor for lg in pool if lg.intensity_factor is not None]
    if len(values) < 3:
        return None
    best = max(values)
    current = analyzed.intensity_factor
    if current > best:
        return PersonalRecord(
            metric="if",
            value=round(current, 3),
            previous_best=round(best, 3),
            sessions_compared=len(values),
            label_fr=f"Meilleur IF sur ce type de séance (IF {current:.2f})",
        )
    return None


def _check_pr_zones_score(analyzed: "AnalyzedSession", pool: list, n: int) -> PersonalRecord | None:
    if analyzed.respect_zones_score is None:
        return None
    values = [lg.respect_zones_score for lg in pool if lg.respect_zones_score is not None]
    if len(values) < 3:
        return None
    best = max(values)
    current = analyzed.respect_zones_score
    if current > best:
        return PersonalRecord(
            metric="zones_score",
            value=round(current, 1),
            previous_best=round(best, 1),
            sessions_compared=len(values),
            label_fr=f"Meilleur respect des zones ({int(current)}/100)",
        )
    return None


def _check_pr_tss(analyzed: "AnalyzedSession", pool: list, n: int) -> PersonalRecord | None:
    if analyzed.tss is None:
        return None
    values = [lg.tss_actual for lg in pool if lg.tss_actual is not None]
    if len(values) < 3:
        return None
    best = max(values)
    current = analyzed.tss
    if current > best:
        return PersonalRecord(
            metric="tss",
            value=round(current, 1),
            previous_best=round(best, 1),
            sessions_compared=len(values),
            label_fr=f"Charge maximale sur ce type de séance (TSS {int(current)})",
        )
    return None


# ---------------------------------------------------------------------------
# Message formatters (utilisés dans webhook.py)
# ---------------------------------------------------------------------------

def build_zone_bars(time_in_zones_s: dict, target_zone: str | None = None) -> str:
    """Construit l'affichage ASCII des zones (barre 10 blocs, min 3%)."""
    total_s = sum(time_in_zones_s.values()) or 1
    sorted_zones = sorted(
        [(z, s) for z, s in time_in_zones_s.items() if s > 0],
        key=lambda x: x[0],
    )
    if not sorted_zones:
        return ""
    lines = ["──────────────────────"]
    for zone, seconds in sorted_zones:
        pct = int(seconds / total_s * 100)
        if pct < 3:
            continue  # Zones négligeables
        filled = round(pct / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        tag = "  \u2190 zone cible" if zone == target_zone else ""
        lines.append(f"{zone} {bar}  {pct}%{tag}")
    lines.append("──────────────────────")
    return "\n".join(lines)


def _plan_comparison_line(
    match_level: str,
    tss_actual: float | None,
    tss_planned: float | None,
) -> str:
    """Ligne courte de comparaison plan/réalisé."""
    if tss_actual is None or tss_planned is None or tss_planned == 0:
        return ""
    pct = (tss_actual / tss_planned - 1) * 100
    if abs(pct) <= 15:
        return "Dans le plan \u2705"
    if pct > 15:
        return f"Au-dessus du plan (+{pct:.0f}%), bien g\u00e9r\u00e9 \u2705"
    return f"En dessous du plan ({pct:.0f}%) \u26a0\ufe0f"


_MATCH_VERDICT: dict[str, str] = {
    "exact": "séance respectée",
    "close": "séance approchée",
    "weak": "séance partielle",
}


def build_message_c_session_card(
    analyzed: "AnalyzedSession",
    session_spec,
    rpe_prompt: str,
    match_level: str = "exact",
    confidence_score: int | None = None,
    day_shift: int = 0,
) -> str:
    """Message C — carte visuelle de séance + demande RPE.

    Remplace l'ancien format : match_text + fitness_feedback + rpe_prompt.
    """
    session_type = analyzed.session_type_real or "unknown"
    icon = _SESSION_ICON.get(session_type, "🚴")
    label = _SESSION_LABEL.get(session_type, session_type.upper())
    duration_min = (analyzed.moving_time_s or analyzed.duration_s) // 60
    tss = analyzed.tss

    lines: list[str] = [f"{icon} {label} · {duration_min} min · TSS {_tss_fmt(tss)}"]

    tss_planned = getattr(session_spec, "tss_target", None) if session_spec else None
    plan_line = _plan_comparison_line(match_level, tss, tss_planned)
    if plan_line:
        lines.append(plan_line)

    if confidence_score is not None:
        verdict = _MATCH_VERDICT.get(match_level, "")
        score_line = f"{verdict} · {confidence_score}/100" if verdict else f"{confidence_score}/100"
        lines.append(score_line)

    if day_shift != 0:
        abs_shift = abs(day_shift)
        unit = "jour" if abs_shift == 1 else "jours"
        direction = "avant" if day_shift < 0 else "après"
        lines.append(f"Réalisé {abs_shift} {unit} {direction}")

    if analyzed.time_in_zones_s:
        target_zone = session_spec.zone_code if session_spec else None
        bars = build_zone_bars(analyzed.time_in_zones_s, target_zone)
        if bars:
            lines.append("")
            lines.append(bars)

    lines.append("")
    lines.append(rpe_prompt)
    return "\n".join(lines)


def build_message_a(highlight: HighlightResult) -> str:
    """Message A — teaser silencieux (2 lignes, sans clavier)."""
    return f"🔍 <i>Analyse de ta sortie...</i>\n\n{highlight.teaser}"


def build_message_b(
    highlight: HighlightResult,
    analyzed: "AnalyzedSession",
    pr: PersonalRecord | None,
) -> str:
    """Message B — métrique héros (sans clavier, sans push notification)."""
    session_type = analyzed.session_type_real or "unknown"
    icon = _SESSION_ICON.get(session_type, "🚴")
    duration_min = (analyzed.moving_time_s or analyzed.duration_s) // 60
    tss = analyzed.tss

    lines: list[str] = []

    # En-tête : type + durée + TSS
    lines.append(f"{icon} {session_type.upper()} · {duration_min} min · TSS {_tss_fmt(tss)}")
    lines.append("")

    # Ligne héros
    pr_badge = " 🏆" if pr else ""
    lines.append(f"{highlight.hero_metric}{pr_badge}")

    # Badge PR
    if pr:
        lines.append(f"({pr.label_fr} !)")

    # Détail de la métrique héros
    if highlight.hero_detail:
        lines.append(highlight.hero_detail)

    # Métriques de support (sauf celles déjà en héros)
    support: list[str] = []
    if (
        analyzed.intensity_factor is not None
        and highlight.category not in ("POWER_PEAK",)
    ):
        support.append(f"IF {analyzed.intensity_factor:.2f}")
    if (
        analyzed.normalized_power is not None
        and highlight.category not in ("POWER_PEAK",)
    ):
        support.append(f"NP {analyzed.normalized_power:.0f}W")
    if analyzed.dominant_zone and highlight.category not in ("VOLUME_CONTEXT",):
        # VOLUME_CONTEXT affiche déjà la zone dans hero_metric ou hero_detail
        support.append(f"Zone dominante {analyzed.dominant_zone}")

    # La distribution des zones est affichée dans le Message C — pas de doublon ici

    if support:
        lines.append(" · ".join(support))

    return "\n".join(lines)
