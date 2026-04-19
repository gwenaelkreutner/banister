"""
KPI d'adhérence au plan d'entraînement — score 0 → 100.

Philosophie :
  - Gamification cumulative : l'athlète part de 0 et vise 100 au jour J.
  - Récompense les comportements positifs, ne pénalise pas l'absence.
  - Seul cas négatif : surcharge (TSB post-séance < seuil niveau).
  - Granularité séance : chaque log contribue immédiatement au score.

Formule par séance :
  budget_séance = (tss_planifiée / tss_semaine) × (100 / N_semaines)
  score         = tss_ratio × zone_mult × (1 + bonus) × malus_overtraining
  pts           = budget_séance × score   (plafonné à 1.5×budget)

Composantes du score :
  tss_ratio   = min(tss_actual / tss_planned, 1.20)   → volume (flexible)
  zone_mult   = 1.0 si zones respectées (Strava)
              = 0.85 si RPE uniquement (pas de Strava)
              = 0.75 si zones incorrectes
  bonus       = +0.10 si long_ride planifié et réalisé (≥ 85% TSS)
              = +0.15 si intervals planifié ET session_type_real == "intervals"
  malus       = ×0.70 si TSB post-séance < seuil surcharge du niveau

Seuils surcharge TSB par niveau :
  beginner     -18
  intermediate -22
  advanced     -28
  expert       -32
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Seuils surcharge TSB ──────────────────────────────────────────────────────

_TSB_OVERTRAINING: dict[str, float] = {
    "beginner":     -18.0,
    "intermediate": -22.0,
    "advanced":     -28.0,
    "expert":       -32.0,
}

# Correspondances workout_type ↔ session_type_real acceptables
_ZONE_COMPAT: dict[str, set[str]] = {
    "long_ride":  {"long_ride", "endurance"},
    "intervals":  {"intervals"},
    "endurance":  {"endurance", "long_ride", "recovery"},
    "recovery":   {"recovery", "endurance"},
}


# ── Résultat ──────────────────────────────────────────────────────────────────

@dataclass
class KPIContribution:
    pts: float            # points gagnés (≥ 0)
    score_session: float  # score interne 0.0–1.5
    reason: str           # message court pour affichage Telegram


# ── Calcul principal ──────────────────────────────────────────────────────────

def compute_session_kpi(
    *,
    tss_planned: float,
    week_tss_planned: float,
    weeks_total: int,
    tss_actual: float | None,
    workout_type: str,
    session_type_real: str | None,
    tsb_after: float | None,
    level: str = "intermediate",
) -> KPIContribution:
    """
    Calcule les points KPI pour une séance loguée.

    Retourne KPIContribution(pts=0, ...) si la séance n'a pas de TSS (non réalisée).

    Args:
        tss_planned         : TSS cible de la séance (SessionSpec.tss_target)
        week_tss_planned    : TSS total planifié de la semaine (WeekPlan.total_tss_target)
        weeks_total         : Nombre de semaines du plan (TrainingPlanSchema.weeks_count)
        tss_actual          : TSS réellement réalisé (session_log.tss_actual)
        workout_type        : Type planifié ("long_ride" | "intervals" | "endurance" | "recovery")
        session_type_real   : Type détecté Strava, None si RPE uniquement
        tsb_after           : TSB post-séance (proxy de la fatigue accumulée)
        level               : Niveau athlète pour le seuil de surcharge
    """
    if not tss_actual or tss_actual <= 0:
        return KPIContribution(pts=0.0, score_session=0.0, reason="non réalisée")

    # ── Budget de la séance ────────────────────────────────────────────────────
    week_budget = 100.0 / max(weeks_total, 1)
    tss_share = tss_planned / max(week_tss_planned, 1.0)
    session_budget = tss_share * week_budget

    # ── Volume compliance ──────────────────────────────────────────────────────
    tss_ratio = min(tss_actual / max(tss_planned, 1.0), 1.20)

    # ── Multiplicateur zone ────────────────────────────────────────────────────
    reason_parts: list[str] = []
    if session_type_real is None:
        zone_mult = 0.85
        reason_parts.append("RPE uniquement")
    elif session_type_real in _ZONE_COMPAT.get(workout_type, {workout_type}):
        zone_mult = 1.0
        reason_parts.append("zones ✓")
    else:
        zone_mult = 0.75
        reason_parts.append("zones incorrectes")

    # ── Bonus type séance ──────────────────────────────────────────────────────
    bonus = 0.0
    if workout_type == "long_ride" and tss_ratio >= 0.85:
        bonus += 0.10
        reason_parts.append("sortie longue ✓")
    if workout_type == "intervals" and session_type_real == "intervals":
        bonus += 0.15
        reason_parts.append("fractionné ✓")

    # ── Malus surcharge — contribution NÉGATIVE ────────────────────────────────
    # Seul cas où le score peut baisser : TSB post-séance sous le seuil du niveau.
    # Proportionnel à l'excès : plus le TSB dépasse le seuil, plus la pénalité est forte.
    # Plafonné à -session_budget (on ne perd jamais plus que le budget de la séance).
    threshold = _TSB_OVERTRAINING.get(level, -22.0)
    if tsb_after is not None and tsb_after < threshold:
        excess = min(abs(tsb_after - threshold) / 15.0, 1.0)  # normalisé 0–1 sur 15 pts de TSB
        pts = -round(session_budget * 0.50 * excess, 2)
        reason_parts.insert(0, f"⚠️ surcharge (TSB {tsb_after:.0f})")
        return KPIContribution(
            pts=pts,
            score_session=round(-0.50 * excess, 3),
            reason=" · ".join(reason_parts),
        )

    # ── Score final (pas de surcharge) ────────────────────────────────────────
    score = tss_ratio * zone_mult * (1.0 + bonus)
    score = min(score, 1.50)

    pts = round(session_budget * score, 2)

    return KPIContribution(
        pts=pts,
        score_session=round(score, 3),
        reason=" · ".join(reason_parts) if reason_parts else "ok",
    )


# ── Utilitaires ───────────────────────────────────────────────────────────────

def compute_cumulative_kpi(contributions: list[float]) -> float:
    """Somme des contributions KPI, bornée à 100."""
    return min(round(sum(c for c in contributions if c), 1), 100.0)


def kpi_delta_message(contribution: KPIContribution) -> str:
    """
    Message court pour affichage Telegram après une séance loguée.

    Exemples :
        "+2.3 pts · sortie longue ✓ · zones ✓"
        "+1.1 pts · ⚠️ surcharge (TSB -31) · zones ✓"
        "+1.8 pts · fractionné ✓ · zones ✓"
    """
    sign = "+" if contribution.pts >= 0 else ""
    return f"{sign}{contribution.pts:.1f} pts · {contribution.reason}"


# ── Affichage gamification ────────────────────────────────────────────────────

_MILESTONES: list[tuple[float, str]] = [
    (25.0, "🌱 <b>Premier quart bouclé — la machine est lancée !</b>"),
    (50.0, "🔥 <b>Mi-chemin atteint — tu tiens le rythme parfait !</b>"),
    (75.0, "🏆 <b>Dernière ligne droite — le jour J approche !</b>"),
]

# Types de séances qui déclenchent un bonus → "séances clés" à mentionner
_KEY_SESSION_TYPES = {"long_ride", "intervals"}


@dataclass
class KPIDisplayData:
    cumulative: float          # score cumulatif actuel (0–100)
    pts_this_session: float    # delta de cette séance
    delta_vs_ideal: float      # écart vs rythme parfait (+ = avance, - = retard)
    pace_message: str          # ligne d'interprétation du rythme
    milestone: str | None      # message milestone si seuil franchi, sinon None
    progress_bar: str          # barre ASCII (20 chars)
    full_block: str            # bloc complet formaté Telegram HTML


def compute_kpi_display(
    *,
    cumulative: float,
    pts_this_session: float,
    week_number: int,
    weeks_total: int,
    plan=None,                     # TrainingPlanSchema (duck-typing) pour next key session
    logged_slots: set | None = None,  # {(week_number, day_of_week)} séances déjà faites
) -> KPIDisplayData:
    """
    Calcule toutes les données d'affichage KPI pour le message post-séance.

    Args:
        cumulative         : score cumulatif incluant la séance courante
        pts_this_session   : contribution de la séance courante (pour delta)
        week_number        : numéro de semaine de la séance (1-indexé)
        weeks_total        : durée totale du plan en semaines
        plan               : TrainingPlanSchema — pour trouver la prochaine séance clé
        logged_slots       : slots déjà loggés — pour exclure du lookup "next key session"
    """
    prev_cumulative = cumulative - pts_this_session

    # ── Rythme idéal ──────────────────────────────────────────────────────────
    ideal = (week_number / max(weeks_total, 1)) * 100.0
    delta = round(cumulative - ideal, 1)

    # ── Message de rythme ─────────────────────────────────────────────────────
    next_key = _find_next_key_session(plan, week_number, logged_slots or set())
    pace_message = _build_pace_message(delta, next_key)

    # ── Milestone (détecté au franchissement de seuil) ────────────────────────
    milestone: str | None = None
    for threshold, text in _MILESTONES:
        if prev_cumulative < threshold <= cumulative:
            milestone = text
            break

    # ── Progress bar ──────────────────────────────────────────────────────────
    bar = _progress_bar(cumulative)

    # ── Assemblage bloc Telegram ──────────────────────────────────────────────
    sign = "+" if pts_this_session >= 0 else ""
    delta_line = f"{sign}{pts_this_session:.1f} pts"

    parts: list[str] = []
    parts.append(f"📊 <b>{delta_line}</b>")
    if milestone:
        parts.append(f"\n{milestone}\n")
    parts.append(f"{bar}  <b>{cumulative:.1f} / 100</b>")
    parts.append(pace_message)

    block = "\n".join(parts)

    return KPIDisplayData(
        cumulative=cumulative,
        pts_this_session=pts_this_session,
        delta_vs_ideal=delta,
        pace_message=pace_message,
        milestone=milestone,
        progress_bar=bar,
        full_block=block,
    )


def _progress_bar(score: float, width: int = 20) -> str:
    """Génère une barre de progression ASCII."""
    score = max(0.0, min(score, 100.0))
    filled = round(score / 100.0 * width)
    return "▓" * filled + "░" * (width - filled)


def _find_next_key_session(plan, from_week: int, logged_slots: set) -> str | None:
    """
    Trouve le type de la prochaine séance clé (long_ride ou intervals)
    non encore réalisée dans le plan, à partir de la semaine courante.
    Retourne "long_ride", "intervals", ou None.
    """
    if plan is None:
        return None
    for week in plan.weeks:
        if week.week_number < from_week:
            continue
        for session in week.sessions:
            if (week.week_number, session.day_of_week) in logged_slots:
                continue
            if session.workout_type in _KEY_SESSION_TYPES:
                return session.workout_type
    return None


def _build_pace_message(delta: float, next_key_session: str | None) -> str:
    """
    Construit la ligne de rythme selon l'écart vs idéal.
    Ne prescrit jamais de séances supplémentaires — focus sur la qualité du prévu.
    """
    if delta >= 5:
        return f"⚡ +{delta:.1f} pts d'avance sur le rythme parfait"
    if delta >= 0:
        return "✅ Dans les temps — continue comme ça"
    if delta >= -5:
        # Petit retard — une bonne séance suffit
        session_label = _session_label(next_key_session)
        return f"🎯 À {abs(delta):.1f} pts du rythme — {session_label} peut tout changer"
    if delta >= -15:
        # Retard modéré — pointer vers la prochaine séance clé sans pression
        session_label = _session_label(next_key_session)
        return f"💪 {abs(delta):.1f} pts sous le rythme — {session_label} est ta priorité"
    # Grand retard — encourager la régularité, pas l'intensité
    return "🔄 Reste régulier — le score se reconstruit séance après séance"


def _session_label(session_type: str | None) -> str:
    """Libellé court pour le type de séance clé."""
    if session_type == "long_ride":
        return "ta prochaine sortie longue"
    if session_type == "intervals":
        return "ton prochain fractionné"
    return "ta prochaine séance clé"


# ── Bloc KPI semaine (post-séance "chaud") ────────────────────────────────────

def compute_weekly_kpi_block(
    *,
    pts_this_session: float,
    weeks_total: int,
    week_pts_list: list[float],
    sessions_done: int,
    sessions_planned: int,
    plan=None,
    week_number: int = 1,
    logged_slots: set | None = None,
) -> str:
    """Bloc KPI post-séance centré sur la semaine en cours.

    Même formule que le KPI plan global, mis à l'échelle semaine (pts × weeks_total).
    Score 0-100 représente la semaine courante — repart à 0 la semaine suivante.
    Le KPI plan global (cumul 0-100 sur l'ensemble du plan) est réservé au /recap.
    """
    import math as _math

    prev_weekly = round((sum(week_pts_list) - pts_this_session) * weeks_total, 1)
    cumulative_weekly = min(round(sum(week_pts_list) * weeks_total, 1), 100.0)

    # ── Milestone (intégré dans le bloc) ──────────────────────────────────────
    milestone_line = ""
    if prev_weekly < 50.0 <= cumulative_weekly:
        milestone_line = "🔥 <b>Mi-semaine franchie — tu tiens le rythme !</b>\n"
    elif prev_weekly < 25.0 <= cumulative_weekly:
        milestone_line = "🌱 <b>Bonne première séance — la semaine est lancée !</b>\n"

    # ── Ronds séances (✅ fait / ◻️ à faire) ──────────────────────────────────
    dots = " ".join(
        ["✅"] * sessions_done + ["◻️"] * max(0, sessions_planned - sessions_done)
    )

    # ── Jauge TSS (charge cumulée, arrondi au supérieur) ──────────────────────
    gauge_width = 12
    filled = round(cumulative_weekly / 100.0 * gauge_width)
    gauge = "[" + "█" * filled + "░" * (gauge_width - filled) + "]"
    pct = _math.ceil(cumulative_weekly)

    # ── Séances restantes ─────────────────────────────────────────────────────
    remaining = max(0, sessions_planned - sessions_done)
    if sessions_done >= sessions_planned:
        pace_msg = "✅ Semaine complète — objectif atteint !"
    elif remaining == 1:
        next_key = _find_next_key_session(plan, week_number, logged_slots or set())
        pace_msg = f"⏳ Plus qu'une séance — {_session_label(next_key)} pour boucler l'objectif."
    else:
        pace_msg = f"⏳ {remaining} séances pour boucler l'objectif."

    return (
        f"<b>OBJECTIF SEMAINE</b>\n"
        f"{milestone_line}"
        f"{dots}  ({sessions_done} / {sessions_planned} séances)\n"
        f"{gauge} {pct}%\n"
        f"{pace_msg}"
    )
