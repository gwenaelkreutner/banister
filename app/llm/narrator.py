"""
Couche narrative LLM.
Reçoit des données structurées du moteur déterministe et génère du texte.
Ne calcule rien — transforme uniquement en narratif FR.
"""

import logging

from app.engine.schemas import TrainingPlanSchema, WeekPlan
from app.llm.prompts import (
    PLAN_SYSTEM_PROMPT,
    PLAN_USER_TEMPLATE,
    WEEK_SYSTEM_PROMPT,
    WEEK_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

DAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
GOAL_FR = {
    "event":       "Événement cible",
    "fitness":     "Forme générale",
    "performance": "Performance",
    "other":       "Objectif personnel",
}
LEVEL_FR = {
    "beginner":     "Débutant",
    "intermediate": "Intermédiaire",
    "advanced":     "Avancé",
    "expert":       "Expert",
}
PHASE_FR = {
    "base":  "Base aérobie",
    "build": "Construction",
    "peak":  "Pic de forme",
    "taper": "Affûtage",
}
WORKOUT_FR = {
    "long_ride":  "Sortie longue",
    "intervals":  "Intervalles",
    "endurance":  "Endurance",
    "recovery":   "Récupération",
}


async def generate_plan_narrative(
    plan: TrainingPlanSchema,
    level: str,
    goal: str,
) -> dict:
    """
    Génère le narratif complet du plan.
    Retourne {"intro": "...", "weeks": {"1": "...", ...}}
    En cas d'erreur API → fallback déterministe.
    """
    try:
        from app.llm.factory import get_provider
        provider = get_provider()

        # Intro générale du plan
        phases_summary = _build_phases_summary(plan)
        ftp_info = _build_ftp_info(plan)

        user_msg = PLAN_USER_TEMPLATE.format(
            level=LEVEL_FR.get(level, level),
            goal=GOAL_FR.get(goal, goal),
            coaching_mode="Puissance (watts)" if plan.coaching_mode == "power" else "Fréquence cardiaque",
            weeks_count=plan.weeks_count,
            ftp_info=ftp_info,
            phases_summary=phases_summary,
        )

        intro = await provider.generate(
            system_prompt=PLAN_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=600,
        )

        return {"intro": intro, "weeks": {}}

    except Exception as e:
        logger.warning(f"Erreur LLM narratif plan : {e} — fallback déterministe")
        return _fallback_plan_narrative(plan, level, goal)


async def generate_week_narrative(
    week: WeekPlan,
    weeks_count: int,
) -> str:
    """Génère le narratif d'une semaine spécifique."""
    try:
        from app.llm.factory import get_provider
        provider = get_provider()

        sessions_detail = _build_sessions_detail(week)
        recovery_note = " (⭐ Semaine de récupération)" if week.is_recovery_week else ""

        user_msg = WEEK_USER_TEMPLATE.format(
            week_number=week.week_number,
            weeks_count=weeks_count,
            phase=PHASE_FR.get(week.phase, week.phase),
            recovery_note=recovery_note,
            tss_target=f"{week.total_tss_target:.0f}",
            sessions_detail=sessions_detail,
        )

        return await provider.generate(
            system_prompt=WEEK_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=250,
        )

    except Exception as e:
        logger.warning(f"Erreur LLM narratif semaine : {e} — fallback")
        return _fallback_week_narrative(week)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_phases_summary(plan: TrainingPlanSchema) -> str:
    lines = []
    seen = set()
    for w in plan.weeks:
        if w.phase not in seen:
            seen.add(w.phase)
            phase_weeks = [x for x in plan.weeks if x.phase == w.phase]
            avg_tss = sum(x.total_tss_target for x in phase_weeks) / len(phase_weeks)
            lines.append(
                f"- {PHASE_FR.get(w.phase, w.phase)} : {len(phase_weeks)} semaines "
                f"(sem. {phase_weeks[0].week_number}–{phase_weeks[-1].week_number})"
                f" — TSS moyen {avg_tss:.0f}/semaine"
            )
    return "\n".join(lines)


def _build_ftp_info(plan: TrainingPlanSchema) -> str:
    if plan.coaching_mode == "power":
        z4 = plan.zones.get("Z4")
        if z4 and z4.lower_watts:
            return f"Zone seuil (Z4) : {z4.lower_watts}–{z4.upper_watts}W"
    else:
        z2 = plan.zones.get("Z2")
        if z2 and z2.lower_bpm:
            return f"Zone endurance (Z2) : {z2.lower_bpm}–{z2.upper_bpm} bpm"
    return ""


def _build_sessions_detail(week: WeekPlan) -> str:
    lines = []
    for s in week.sessions:
        day = DAY_NAMES_FR[s.day_of_week]
        workout = WORKOUT_FR.get(s.workout_type, s.workout_type)
        lines.append(
            f"- {day} : {workout} {s.zone_code} — {s.duration_minutes}min "
            f"(TSS ~{s.tss_target:.0f})"
        )
    return "\n".join(lines)


# ── Fallbacks déterministes ──────────────────────────────────────────────────

def _fallback_plan_narrative(plan: TrainingPlanSchema, level: str, goal: str) -> dict:
    phases = _build_phases_summary(plan)
    intro = (
        f"🚴 Ton plan de {plan.weeks_count} semaines est prêt !\n\n"
        f"Il est conçu pour un athlète de niveau {LEVEL_FR.get(level, level)} "
        f"visant {GOAL_FR.get(goal, goal)}.\n\n"
        f"📅 Phases :\n{phases}\n\n"
        f"💪 Bonne préparation ! Tape /plan pour voir ta première semaine."
    )
    return {"intro": intro, "weeks": {}}


def _fallback_week_narrative(week: WeekPlan) -> str:
    phase = PHASE_FR.get(week.phase, week.phase)
    recovery = " — Semaine de récupération 🟢" if week.is_recovery_week else ""
    sessions_str = _build_sessions_detail(week)
    return (
        f"📅 Semaine {week.week_number} — {phase}{recovery}\n"
        f"TSS cible : {week.total_tss_target:.0f}\n\n"
        f"{sessions_str}"
    )
