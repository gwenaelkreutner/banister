"""
Couche narrative LLM.
Reçoit des données structurées du moteur déterministe et génère du texte.
Ne calcule rien — transforme uniquement en narratif FR.
"""

import logging

from app.config import settings
from app.core.localization import t
from app.engine.schemas import TrainingPlanSchema, WeekPlan
from app.llm.prompts import (
    plan_intro_system_prompt,
    plan_intro_user_message,
    week_system_prompt,
    week_user_message,
)

logger = logging.getLogger(__name__)

_DAY_KEYS = [
    "day.full.monday", "day.full.tuesday", "day.full.wednesday", "day.full.thursday",
    "day.full.friday", "day.full.saturday", "day.full.sunday",
]
_GOAL_KEYS = {
    "event": "llm.goal.event", "fitness": "llm.goal.fitness",
    "performance": "llm.goal.performance", "other": "llm.goal.other",
}
_LEVEL_KEYS = {
    "beginner": "llm.level.beginner", "intermediate": "llm.level.intermediate",
    "advanced": "llm.level.advanced", "expert": "llm.level.expert",
}
_PHASE_KEYS = {
    "base": "llm.phase.base", "build": "llm.phase.build",
    "peak": "llm.phase.peak", "taper": "llm.phase.taper",
}
_WORKOUT_KEYS = {
    "long_ride": "session.workout.long_ride", "intervals": "session.workout.intervals",
    "endurance": "session.workout.endurance", "recovery": "session.workout.recovery",
}


def _day_name(dow: int) -> str:
    return t(_DAY_KEYS[dow])


def goal_label(goal: str) -> str:
    key = _GOAL_KEYS.get(goal)
    return t(key) if key else goal


def level_label(level: str) -> str:
    key = _LEVEL_KEYS.get(level)
    return t(key) if key else level


def phase_label(phase: str) -> str:
    key = _PHASE_KEYS.get(phase)
    return t(key) if key else phase


def workout_label(workout_type: str) -> str:
    key = _WORKOUT_KEYS.get(workout_type)
    return t(key) if key else workout_type


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

        coaching_mode_label = (
            t("llm.coaching_mode.power") if plan.coaching_mode == "power"
            else t("llm.coaching_mode.hr")
        )
        user_msg = plan_intro_user_message(
            level=level_label(level),
            goal=goal_label(goal),
            coaching_mode=coaching_mode_label,
            weeks_count=plan.weeks_count,
            ftp_info=ftp_info,
            phases_summary=phases_summary,
        )

        intro = await provider.generate(
            system_prompt=plan_intro_system_prompt(),
            user_message=user_msg,
            max_tokens=settings.llm_max_tokens,
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
        recovery_note = t("narrator.recovery_week_note") if week.is_recovery_week else ""

        user_msg = week_user_message(
            week_number=week.week_number,
            weeks_count=weeks_count,
            phase=phase_label(week.phase),
            recovery_note=recovery_note,
            tss_target=f"{week.total_tss_target:.0f}",
            sessions_detail=sessions_detail,
        )

        return await provider.generate(
            system_prompt=week_system_prompt(),
            user_message=user_msg,
            max_tokens=settings.llm_max_tokens,
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
            lines.append(t(
                "narrator.phase_summary_line",
                phase=phase_label(w.phase),
                weeks=len(phase_weeks),
                first=phase_weeks[0].week_number,
                last=phase_weeks[-1].week_number,
                avg=f"{avg_tss:.0f}",
            ))
    return "\n".join(lines)


def _build_ftp_info(plan: TrainingPlanSchema) -> str:
    if plan.coaching_mode == "power":
        z4 = plan.zones.get("Z4")
        if z4 and z4.lower_watts:
            return t("narrator.zone_threshold_info", low=z4.lower_watts, high=z4.upper_watts)
    else:
        z2 = plan.zones.get("Z2")
        if z2 and z2.lower_bpm:
            return t("narrator.zone_endurance_info", low=z2.lower_bpm, high=z2.upper_bpm)
    return ""


def _build_sessions_detail(week: WeekPlan) -> str:
    lines = []
    for s in week.sessions:
        lines.append(t(
            "narrator.session_line",
            day=_day_name(s.day_of_week),
            workout=workout_label(s.workout_type),
            zone=s.zone_code,
            duration=s.duration_minutes,
            tss=f"{s.tss_target:.0f}",
        ))
    return "\n".join(lines)


# ── Fallbacks déterministes ──────────────────────────────────────────────────

def _fallback_plan_narrative(plan: TrainingPlanSchema, level: str, goal: str) -> dict:
    phases = _build_phases_summary(plan)
    intro = t(
        "narrator.fallback_plan_intro",
        weeks_count=plan.weeks_count,
        level=level_label(level),
        goal=goal_label(goal),
        phases=phases,
    )
    return {"intro": intro, "weeks": {}}


def _fallback_week_narrative(week: WeekPlan) -> str:
    recovery = t("narrator.fallback_week_recovery_note") if week.is_recovery_week else ""
    sessions_str = _build_sessions_detail(week)
    return t(
        "narrator.fallback_week",
        week_number=week.week_number,
        phase=phase_label(week.phase),
        recovery=recovery,
        tss_target=f"{week.total_tss_target:.0f}",
        sessions=sessions_str,
    )
