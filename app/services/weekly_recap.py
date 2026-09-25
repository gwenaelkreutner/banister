"""
Service : bilan hebdomadaire.

Agrège les données de la semaine (moteur déterministe) et génère l'analyse LLM.
Utilisé par le handler /recap et le scheduler dimanche 20h.

Principe : le LLM interprète uniquement — tous les chiffres viennent du moteur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db import repositories as repo
from app.db.models.user import User
from app.engine.atl_ctl import compute_fitness_from_any, estimate_initial_ctl, tsb_label
from app.engine.guardrail_thresholds import MONOTONY_HIGH
from app.engine.schemas import TrainingPlanSchema
from app.engine.tid import compute_tid
from app.engine.tss import tss_from_weekly_hours
from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)


@dataclass
class WeeklyRecapResult:
    stats_section: str        # HTML déterministe — safe à envoyer directement
    coach_section: str        # LLM ou fallback déterministe
    next_week_section: str    # LLM ou fallback déterministe
    has_data: bool            # False si aucun log/activité
    next_week_number: int | None = None  # Numéro de semaine S+1 dans le plan (pour /week N)


# ── Helpers internes ──────────────────────────────────────────────────────────

def _item_date(it):
    return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)


def _item_tss(it):
    return getattr(it, "tss_actual", None) or getattr(it, "tss", None)


def _recap_tone_directive(snapshot: WeeklySnapshot, tsb: float, compliance_pct: float | None) -> str:
    """Détermine la directive tonalité pour le LLM selon l'état de la semaine."""
    from app.llm.prompts import recap_tone_directive

    comp = compliance_pct if compliance_pct is not None else 100.0
    if tsb < -30 or comp < 30:
        return recap_tone_directive("critical")
    if snapshot.load_trend_pct > 20 and comp >= 80:
        return recap_tone_directive("celebratory")
    if comp >= 80 and tsb > -10:
        return recap_tone_directive("enthusiastic")
    if comp < 50:
        return recap_tone_directive("understanding")
    return recap_tone_directive("balanced")


async def _estimate_ctl_seed(items: list, session: AsyncSession, user_id) -> float:
    """
    Retourne un CTL initial si la fenêtre de données est < 84j.
    Copié depuis forme.py — évite la sous-estimation EMA sur courte période.
    """
    if not items:
        return 0.0

    oldest = min(_item_date(it) for it in items if _item_date(it))
    if oldest is None:
        return 0.0
    span_days = (date.today() - oldest).days
    if span_days >= 84:
        return 0.0

    profile_db = await repo.profile_repo.get_by_user_id(session, user_id)
    if not profile_db:
        return 0.0

    profile_data = profile_db.profile or {}
    availability = profile_data.get("availability") or {}
    hours = availability.get("hours_per_week", 0)
    if not hours:
        return 0.0

    weekly_tss = tss_from_weekly_hours(float(hours))
    return estimate_initial_ctl(weekly_tss)


def _format_stats_section(
    snapshot: WeeklySnapshot,
    today: date,
    sessions_done: int,
    sessions_planned: int | None,
    compliance_pct: float | None,
) -> str:
    """Construit la section stats en HTML pur (pas de LLM)."""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    trend = snapshot.load_trend_pct
    trend_icon = "▲" if trend >= 0 else "▼"
    trend_str = f"{trend_icon} {abs(trend):.0f}%"

    if sessions_planned is not None:
        sessions_line = t(
            "weekly_recap.sessions_with_plan",
            done=sessions_done, planned=sessions_planned, pct=f"{compliance_pct:.0f}",
        )
    else:
        sessions_line = t("weekly_recap.sessions_no_plan", done=sessions_done)

    monotony_line = ""
    if snapshot.monotony_index is not None and snapshot.monotony_index > MONOTONY_HIGH:
        monotony_line = t(
            "weekly_recap.monotony_warning", index=f"{snapshot.monotony_index:.1f}"
        )

    return t(
        "weekly_recap.stats_section",
        start=monday.strftime("%d/%m"),
        end=sunday.strftime("%d/%m/%Y"),
        tss7d=f"{snapshot.tss_7d:.0f}",
        tss6w=f"{snapshot.tss_6w_avg:.0f}",
        trend=trend_str,
        sessions_line=sessions_line,
        monotony_line=monotony_line,
    )


# ── Service principal ─────────────────────────────────────────────────────────

async def compute_weekly_recap(
    session: AsyncSession,
    user: User,
    today: date | None = None,
) -> WeeklyRecapResult:
    """
    Calcule le bilan hebdomadaire complet pour un utilisateur.

    Utilisable depuis le handler /recap (session injectée par middleware)
    ou depuis le scheduler (session ouverte par le broadcast).
    """
    today = today or date.today()

    # ── Chargement des données ────────────────────────────────────────────────
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    plan_start = plan.start_date if plan else today

    activities = await repo.activity_repo.get_for_user(session, user.id, days=90)
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]

    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    profile_db = await repo.profile_repo.get_by_user_id(session, user.id)

    all_items = pre_plan_acts + logs

    if not all_items:
        return WeeklyRecapResult(
            stats_section="", coach_section="", next_week_section="", has_data=False
        )

    # ── Métriques déterministes ───────────────────────────────────────────────
    snapshot = compute_weekly_snapshot(all_items, today)
    tid = compute_tid(all_items, today=today, window_days=7)

    current = await get_current_fitness(session, user.id, today=today)
    if current is not None:
        fitness = current.metrics
    else:
        initial_ctl = await _estimate_ctl_seed(all_items, session, user.id)
        seed_date = (today - timedelta(days=49)) if initial_ctl > 0 else None
        fitness = compute_fitness_from_any(all_items, initial_ctl=initial_ctl, seed_date=seed_date)
    tsb = fitness.tsb
    tsb_lbl = tsb_label(tsb)

    # ── Compliance plan ───────────────────────────────────────────────────────
    sessions_planned: int | None = None
    compliance_pct: float | None = None
    week_num: int | None = None
    current_phase: str = "base"
    next_week_obj = None
    next_week_number: int | None = None

    if plan:
        try:
            schema = TrainingPlanSchema.model_validate(plan.plan_technical)
            week_num = max(1, (today - plan.start_date).days // 7 + 1)
            current_week = next((w for w in schema.weeks if w.week_number == week_num), None)
            if current_week:
                sessions_planned = len(current_week.sessions)
                current_phase = current_week.phase
                compliance_pct = (
                    snapshot.sessions_done_7d / sessions_planned * 100
                    if sessions_planned > 0 else None
                )
            next_week_obj = next((w for w in schema.weeks if w.week_number == week_num + 1), None)
            if next_week_obj:
                next_week_number = week_num + 1
        except Exception:
            logger.warning("Erreur lecture plan pour récap — compliance ignorée")

    # ── Données profil pour LLM ───────────────────────────────────────────────
    profile_data = (profile_db.profile or {}) if profile_db else {}
    user_level: int = profile_data.get("user_level", 0)
    from app.llm.narrator import goal_label, level_label

    level_fr: str = level_label(profile_data.get("level", "beginner"))
    goal_obj = profile_data.get("objective") or {}
    goal_fr: str = goal_label(goal_obj.get("type", "fitness") if isinstance(goal_obj, dict) else "fitness")
    ftp_watts = profile_data.get("ftp", "N/A")
    hr_max = profile_data.get("hr_max")
    hr_line = f"\n- FC max : {hr_max} bpm" if hr_max else ""

    # ── Section stats (déterministe) ──────────────────────────────────────────
    stats_section = _format_stats_section(
        snapshot, today, snapshot.sessions_done_7d, sessions_planned, compliance_pct
    )

    # ── Variables LLM ─────────────────────────────────────────────────────────
    tone_directive = _recap_tone_directive(snapshot, tsb, compliance_pct)
    sessions_done_display = snapshot.sessions_done_7d
    sessions_planned_display = sessions_planned if sessions_planned is not None else "N/A"
    compliance_display = compliance_pct if compliance_pct is not None else 0.0
    monotony_line = (
        t("llm.recap_monotony_line", index=f"{snapshot.monotony_index:.1f}")
        if snapshot.monotony_index is not None and snapshot.monotony_index > MONOTONY_HIGH
        else ""
    )
    tid_line = ""
    if tid is not None:
        from app.llm.prompts import tid_classification_label

        tid_line = t(
            "llm.recap_tid_line",
            label=tid_classification_label(tid.classification),
            z1=f"{tid.zone1_pct:.0f}", z2=f"{tid.zone2_pct:.0f}", z3=f"{tid.zone3_pct:.0f}",
        )

    # ── Séances semaine prochaine ─────────────────────────────────────────────
    next_phase = next_week_obj.phase if next_week_obj else current_phase
    next_tss_target = f"{next_week_obj.total_tss_target:.0f}" if next_week_obj else "N/A"
    recovery_flag = (
        t("llm.recap_recovery_flag") if (next_week_obj and next_week_obj.is_recovery_week) else ""
    )
    if next_week_obj and next_week_obj.sessions:
        next_sessions_detail = "\n".join(
            f"  • {s.description_fr} ({s.zone_code}, {s.duration_minutes}min, ~{s.tss_target:.0f} TSS)"
            for s in next_week_obj.sessions
        )
    else:
        next_sessions_detail = t("llm.recap_no_next_sessions")

    # ── Appels LLM ────────────────────────────────────────────────────────────
    coach_section = await _generate_coach_section(
        tss_7d=snapshot.tss_7d,
        tss_6w_avg=snapshot.tss_6w_avg,
        load_trend_pct=snapshot.load_trend_pct,
        sessions_done=sessions_done_display,
        sessions_planned=sessions_planned_display,
        compliance_pct=compliance_display,
        monotony_line=monotony_line,
        tid_line=tid_line,
        tsb=tsb,
        tsb_label=tsb_lbl,
        tone_directive=tone_directive,
        level_fr=level_fr,
        goal_fr=goal_fr,
        ftp_watts=ftp_watts,
        hr_line=hr_line,
        user_level=user_level,
    )

    next_week_section = await _generate_nextweek_section(
        tss_7d=snapshot.tss_7d,
        load_trend_pct=snapshot.load_trend_pct,
        compliance_pct=compliance_display,
        tsb=tsb,
        tsb_label=tsb_lbl,
        next_phase=next_phase,
        recovery_flag=recovery_flag,
        next_tss_target=next_tss_target,
        next_sessions_detail=next_sessions_detail,
    )

    # ── Persistance adhérence ─────────────────────────────────────────────────
    monday = today - timedelta(days=today.weekday())
    try:
        await repo.weekly_adherence_repo.upsert(
            session,
            user_id=user.id,
            week_start_date=monday,
            sessions_done=snapshot.sessions_done_7d,
            tss_7d=snapshot.tss_7d,
            plan_id=plan.id if plan else None,
            week_number=week_num,
            sessions_planned=sessions_planned,
            compliance_pct=compliance_pct,
        )
    except Exception:
        logger.warning("Erreur persistance weekly_adherence — ignorée")

    return WeeklyRecapResult(
        stats_section=stats_section,
        coach_section=coach_section,
        next_week_section=next_week_section,
        has_data=True,
        next_week_number=next_week_number,
    )


# ── Génération LLM avec fallbacks ─────────────────────────────────────────────

async def _generate_coach_section(
    tss_7d, tss_6w_avg, load_trend_pct, sessions_done, sessions_planned,
    compliance_pct, monotony_line, tid_line, tsb, tsb_label, tone_directive,
    level_fr, goal_fr, ftp_watts, hr_line, user_level,
) -> str:
    try:
        from app.llm.factory import get_provider
        from app.llm.prompts import weekly_recap_coach_message, weekly_recap_system_prompt

        prompt = weekly_recap_coach_message(
            level_fr=level_fr,
            goal_fr=goal_fr,
            ftp_watts=ftp_watts,
            hr_line=hr_line,
            tss_7d=f"{tss_7d:.0f}",
            tss_6w_avg=f"{tss_6w_avg:.0f}",
            load_trend_pct=load_trend_pct,
            sessions_done=sessions_done,
            sessions_planned=sessions_planned,
            compliance_pct=compliance_pct,
            monotony_line=monotony_line,
            tid_line=tid_line,
            tsb=tsb,
            tsb_label=tsb_label,
            tone_directive=tone_directive,
        )
        provider = get_provider()
        return await provider.generate(
            system_prompt=weekly_recap_system_prompt(),
            user_message=prompt,
            max_tokens=4000,
        )
    except Exception as e:
        logger.warning(f"Erreur LLM coach section : {e} — fallback")
        return _fallback_coach(load_trend_pct, compliance_pct)


async def _generate_nextweek_section(
    tss_7d, load_trend_pct, compliance_pct, tsb, tsb_label,
    next_phase, recovery_flag, next_tss_target, next_sessions_detail,
) -> str:
    try:
        from app.llm.factory import get_provider
        from app.llm.prompts import weekly_recap_nextweek_message, weekly_recap_system_prompt

        prompt = weekly_recap_nextweek_message(
            tss_7d=f"{tss_7d:.0f}",
            load_trend_pct=load_trend_pct,
            compliance_pct=compliance_pct,
            tsb=tsb,
            tsb_label=tsb_label,
            next_phase=next_phase,
            recovery_flag=recovery_flag,
            next_tss_target=next_tss_target,
            next_sessions_detail=next_sessions_detail,
        )
        provider = get_provider()
        return await provider.generate(
            system_prompt=weekly_recap_system_prompt(),
            user_message=prompt,
            max_tokens=4000,
        )
    except Exception as e:
        logger.warning(f"Erreur LLM nextweek section : {e} — fallback")
        return _fallback_nextweek(tsb)


def _fallback_coach(load_trend_pct: float, compliance_pct: float | None) -> str:
    comp = compliance_pct if compliance_pct is not None else 100.0
    if comp < 30:
        return t("weekly_recap.fallback_coach_critical")
    if load_trend_pct > 20 and comp >= 80:
        return t("weekly_recap.fallback_coach_great")
    if comp >= 80:
        return t("weekly_recap.fallback_coach_good")
    return t("weekly_recap.fallback_coach_ok")


def _fallback_nextweek(tsb: float) -> str:
    if tsb < -10:
        return t("weekly_recap.fallback_nextweek_fatigued")
    if tsb > 10:
        return t("weekly_recap.fallback_nextweek_fresh")
    return t("weekly_recap.fallback_nextweek_balanced")
