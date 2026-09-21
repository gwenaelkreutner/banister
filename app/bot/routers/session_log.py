"""
Router : capture du ressenti (RPE) post-séance.

Toute activité vient désormais de la source (intervals.icu) via le poller — voir
app/providers/intervals/notifier.py, qui déclenche la notification stagée se terminant
par le clavier RPE géré ici (callback log:rpe:*).

Le log manuel de séance (spec 002 FR-035) a été retiré : plus de saisie de durée, plus
de bouton "Séance faite/Sautée", plus d'état FSM dédié. La capture du ressenti, elle,
est préservée à l'identique (FR-031, FR-037) — les deux flux partageaient déjà cette
même implémentation.
"""

import asyncio
import logging
import uuid
from html import escape
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics, compute_fitness_from_any, estimate_initial_ctl
from app.engine.adherence_kpi import compute_session_kpi, compute_weekly_kpi_block
from app.engine.tss import detect_fatigue_anomaly_scalar, tss_from_weekly_hours
from app.engine.schemas import TrainingPlanSchema
from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)
router = Router()

_DOW_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_TYPE_FR = {
    "long_ride": "Sortie longue",
    "intervals": "Intervalles",
    "endurance": "Endurance",
    "recovery": "Récupération",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_spec(plan, week_num: int, dow: int):
    """Retourne le SessionSpec pour une séance donnée (semaine, jour), ou None."""
    if plan is None or week_num is None:
        return None
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    for week in schema.weeks:
        if week.week_number == week_num:
            for sess in week.sessions:
                if sess.day_of_week == dow:
                    return sess
            break
    return None


def _get_sessions_planned_week(plan, week_num: int) -> int | None:
    """Retourne le nombre de séances prévues pour la semaine donnée."""
    if plan is None or week_num is None:
        return None
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    for week in schema.weeks:
        if week.week_number == week_num:
            return len(week.sessions)
    return None


def _get_next_session_info(plan, week_num: int, current_dow: int) -> str | None:
    """Retourne une description de la prochaine séance planifiée après current_dow.

    Cherche d'abord dans la semaine courante, puis dans la semaine suivante.
    """
    if plan is None or week_num is None:
        return None
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)

    weeks_by_num = {w.week_number: w for w in schema.weeks}

    # Semaine courante : sessions après current_dow
    current_week = weeks_by_num.get(week_num)
    if current_week:
        upcoming = sorted(
            [s for s in current_week.sessions if s.day_of_week > current_dow],
            key=lambda s: s.day_of_week,
        )
        if upcoming:
            s = upcoming[0]
            dow_label = _DOW_FR[s.day_of_week]
            type_label = _TYPE_FR.get(s.workout_type, s.workout_type)
            return f"{dow_label} — {type_label} {s.zone_code}, {s.duration_minutes} min ({s.tss_target:.0f} TSS)"

    # Semaine suivante : première session
    next_week = weeks_by_num.get(week_num + 1)
    if next_week:
        upcoming = sorted(next_week.sessions, key=lambda s: s.day_of_week)
        if upcoming:
            s = upcoming[0]
            dow_label = _DOW_FR[s.day_of_week]
            type_label = _TYPE_FR.get(s.workout_type, s.workout_type)
            return f"{dow_label} (sem. suivante) — {type_label} {s.zone_code}, {s.duration_minutes} min ({s.tss_target:.0f} TSS)"

    return None


async def _get_fitness_metrics(session, user_id, logs: list) -> tuple[FitnessMetrics, list]:
    """ATL/CTL/TSB — consommés depuis la source (spec 002 FR-016, app/services/fitness.py),
    recalcul local uniquement en repli si aucun wellness n'a encore été ingéré.

    Retourne (metrics, all_items) pour que l'appelant puisse passer all_items au snapshot —
    all_items reste nécessaire même côté source, car le snapshot hebdo (tendance, monotonie)
    n'a pas d'équivalent dans le payload wellness.
    """
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    plan_start = plan.start_date if plan else date.today()

    activities = await repo.activity_repo.get_for_user(session, user_id, days=365)
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]

    all_items = pre_plan_acts + logs

    if not all_items:
        return FitnessMetrics(ctl=0.0, atl=0.0, tsb=0.0), []

    current = await get_current_fitness(session, user_id)
    if current is not None:
        return current.metrics, all_items

    # Amorçage CTL si fenêtre < 84j
    oldest = min(
        (getattr(it, "logged_date", None) or getattr(it, "activity_date", None))
        for it in all_items
    )
    span_days = (date.today() - oldest).days
    initial_ctl = 0.0
    if span_days < 84:
        profile_db = await repo.profile_repo.get_by_user_id(session, user_id)
        if profile_db:
            profile_data = profile_db.profile or {}
            hours = (profile_data.get("availability") or {}).get("hours_per_week", 0)
            if hours:
                initial_ctl = estimate_initial_ctl(tss_from_weekly_hours(hours))

    seed_date = (date.today() - timedelta(days=49)) if initial_ctl > 0 else None
    metrics = compute_fitness_from_any(all_items, initial_ctl=initial_ctl, seed_date=seed_date)
    return metrics, all_items


def _compute_zones_pct(time_in_zones_s: dict | None) -> dict | None:
    """Convertit les secondes par zone en pourcentages (pour le prompt LLM)."""
    if not time_in_zones_s:
        return None
    total = sum(time_in_zones_s.values()) or 1
    return {z: int(s / total * 100) for z, s in time_in_zones_s.items() if s > 0}


def _extract_next_day(next_session_info: str | None) -> str | None:
    """Extrait le jour de la prochaine séance depuis la chaîne formatée."""
    if not next_session_info:
        return None
    return next_session_info.split("—")[0].strip()


def _build_coach_message(
    blocks: dict[str, str],
    next_day: str | None,
    kpi_block: str | None,
    pts_weekly: int | None = None,
) -> str:
    """Construit le Message D depuis les blocs LLM + KPI semaine (parse_mode HTML)."""
    parts: list[str] = []
    form = blocks.get("form_interpretation", "")
    session_interp = blocks.get("session_interpretation", "")
    advice = blocks.get("next_advice", "")
    if form:
        parts.append(f"📉 État de forme\n{escape(form)}")
    if session_interp:
        gain = f"  <b>+{pts_weekly} pts</b> ✨" if pts_weekly is not None else ""
        parts.append(f"🎯 Ta séance\n{escape(session_interp)}{gain}")
    next_label = f"📅 Prochaine étape{' — ' + next_day if next_day else ''}"
    if advice:
        parts.append(f"{next_label}\n{escape(advice)}")
    text = "\n\n".join(parts)
    if kpi_block:
        text += f"\n\n{kpi_block}"
    return text


async def _reveal_activity_analysis(message, *, kpi_block: str | None = None, **kwargs) -> None:
    """Révèle l'analyse coach en éditant C puis en envoyant le Message D."""
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        await asyncio.sleep(1.5)
        from app.llm.activity_analysis import generate_coach_blocks
        from app.engine.atl_ctl import tsb_label as _tsb_label

        snap = kwargs.get("weekly_snapshot")
        tsb = kwargs.get("tsb")
        blocks = await generate_coach_blocks(
            tsb=tsb,
            tsb_label_str=_tsb_label(tsb) if tsb is not None else None,
            load_trend_pct=snap.load_trend_pct if snap else None,
            tss_6w_daily_avg=(snap.tss_6w_avg / 7) if snap and snap.tss_6w_avg else None,
            sessions_done_week=kwargs.get("sessions_done_week"),
            sessions_planned_week=kwargs.get("sessions_planned_week"),
            tss_actual=kwargs.get("actual_tss"),
            tss_planned=kwargs.get("planned_tss"),
            session_type_real=kwargs.get("session_type_real"),
            planned_workout_type=kwargs.get("planned_workout_type"),
            dominant_zone=kwargs.get("dominant_zone"),
            time_in_zones_pct=_compute_zones_pct(kwargs.get("time_in_zones_s")),
            rpe=kwargs.get("rpe"),
            next_session_info=kwargs.get("next_session_info"),
            user_level=kwargs.get("user_level", 0),
        )
        next_day = _extract_next_day(kwargs.get("next_session_info"))
        pts_weekly = kwargs.get("pts_weekly")
        # Nettoyer le message C (retire "Ton coach analyse...")
        await message.edit_text("✅ Ressenti noté.", parse_mode="HTML")
        # Envoyer le Message D
        await message.bot.send_message(
            message.chat.id,
            _build_coach_message(blocks, next_day, kpi_block, pts_weekly),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("[_reveal_activity_analysis] Erreur : %s", exc)
        try:
            await message.edit_text("✅ Ressenti noté.", parse_mode="HTML")
        except Exception:
            pass


def _select_storytelling_mode(session_type_real: str | None) -> str:
    """Sélectionne le mode narratif LLM selon le type de séance (tirage pondéré)."""
    import random
    _MODE_WEIGHTS: dict[str, list[float]] = {
        # [journalist, analyst, coach]
        "intervals": [0.5, 0.3, 0.2],
        "race":      [0.7, 0.2, 0.1],
        "long_ride": [0.3, 0.4, 0.3],
        "endurance": [0.2, 0.5, 0.3],
        "recovery":  [0.1, 0.2, 0.7],
        "tempo":     [0.4, 0.4, 0.2],
    }
    modes = ["journalist", "analyst", "coach"]
    weights = _MODE_WEIGHTS.get(session_type_real or "", [0.33, 0.33, 0.34])
    return random.choices(modes, weights=weights, k=1)[0]


def _highlight_category_from_log(log) -> str | None:
    """Dérive la catégorie highlight la plus notable du log (déterministe, sans aléatoire).

    Utilisé pour alimenter le prompt LLM — ordre = priorité décroissante.
    """
    ci = log.intervals_consistency_index
    if_ = log.intensity_factor
    drift = log.cardiac_drift_index
    zones_score = log.respect_zones_score

    if ci is not None and ci >= 0.88:
        return "CONSISTENCY_KING"
    if if_ is not None and if_ >= 0.95:
        return "POWER_PEAK"
    if drift is not None and abs(drift) >= 0.08:
        return "CARDIAC_STORY"
    if zones_score is not None and (zones_score >= 90 or zones_score <= 60):
        return "ZONE_DISCIPLINE"
    return None


# ── RPE ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("log:rpe:"))
async def cb_rpe(callback: CallbackQuery, session: AsyncSession, user: User):
    # Format : log:rpe:{log_id}:{valeur 1-10 | "skip"} — le clavier envoie une valeur
    # représentative sur l'échelle standard (app/engine/rpe.py), plus un token emoji.
    parts = callback.data.split(":")
    log_id_str = parts[2]
    rpe_token = parts[3]

    log = await repo.session_log_repo.get_by_id(session, uuid.UUID(log_id_str))

    if log is None or log.user_id != user.id:
        await callback.answer("Log introuvable.", show_alert=True)
        return

    # Calculée une fois — spec 002 T065 (FR-041) : une seconde évaluation identique de
    # cette condition plus loin aurait laissé une variable dont la disponibilité dépend
    # de deux endroits restant en phase, un NameError latent si un seul est édité.
    rpe_effective = float(rpe_token) if rpe_token != "skip" else None
    if rpe_effective is not None:
        log.rpe = rpe_effective

    # Édition immédiate : supprime le clavier RPE, affiche l'état "chargement"
    await callback.message.edit_text(
        "✅ Ressenti noté.\n\n🔍 <i>Ton coach analyse...</i>",
        parse_mode="HTML",
    )
    await callback.answer()

    # Charger les logs + activités pré-plan pour fitness cohérente avec /forme
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    metrics, all_items = await _get_fitness_metrics(session, user.id, logs)

    # Analyse LLM post-RPE (non-bloquant) — contexte enrichi depuis SessionLog
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    _spec = _get_session_spec(plan, log.week_number, log.day_of_week) if plan and log.week_number is not None else None
    planned_tss = _spec.tss_target if _spec else None
    planned_duration = _spec.duration_minutes if _spec else None
    planned_workout_type = _spec.workout_type if _spec else None
    profile_db = await repo.profile_repo.get_by_user_id(session, user.id)
    profile_data = (profile_db.profile or {}) if profile_db else {}
    user_level: int = profile_data.get("user_level", 0)

    # ── Détection anomalie fatigue (HRSS scalaire) ────────────────────────
    fatigue_anomaly: dict | None = None
    if rpe_effective and log.avg_heart_rate:
        hr_max = profile_data.get("physio", {}).get("hr_max")
        hr_rest = profile_data.get("physio", {}).get("hr_rest")
        sex = profile_data.get("sex")
        if hr_max and hr_rest is not None:
            fa = detect_fatigue_anomaly_scalar(
                avg_hr=float(log.avg_heart_rate),
                hr_rest=hr_rest,
                hr_max=hr_max,
                user_rpe=round(rpe_effective),
                sex=sex or "M",
            )
            if fa is not None:
                import dataclasses
                fatigue_anomaly = dataclasses.asdict(fa)

    # ── Variable Reward — sélection du mode et détection PR ───────────────
    from app.providers.analysis.highlight import detect_personal_records
    from app.db.models.session_log import SessionLog as SessionLogModel

    storytelling_mode = _select_storytelling_mode(log.session_type_real)
    highlight_category = _highlight_category_from_log(log)

    session_logs_only = [it for it in all_items if isinstance(it, SessionLogModel)]
    pr_result = detect_personal_records(
        session_logs_only,
        session_type_real=log.session_type_real,
        tss=log.tss_actual,
        intensity_factor=log.intensity_factor,
        intervals_consistency_index=log.intervals_consistency_index,
        respect_zones_score=log.respect_zones_score,
        current_log_id=log.id,
    )

    personal_record_dict = None
    if pr_result is not None:
        import dataclasses
        personal_record_dict = dataclasses.asdict(pr_result)

    snap = compute_weekly_snapshot(all_items, date.today())
    _today_s = date.today()
    _monday_s = _today_s - timedelta(days=_today_s.weekday())
    _sessions_done_week_s = sum(
        1 for it in all_items
        if getattr(it, "status", None) == "done"
        and getattr(it, "logged_date", None) is not None
        and it.logged_date >= _monday_s
    )

    # ── KPI block (semaine en cours) ──────────────────────────────────────────
    # spec 002 T065 (FR-041): kpi_block and pts_weekly_s both need plan_schema_kpi, and
    # both are only meaningful under the same "a contribution and a plan exist" gate —
    # computed together in the one place that condition is checked, rather than in two
    # separate blocks that would have to keep an identical guard in sync by hand.
    kpi_block: str | None = None
    pts_weekly_s: int | None = None
    if log.kpi_contribution is not None and plan:
        plan_schema_kpi = TrainingPlanSchema.model_validate(plan.plan_technical)
        session_logs_for_kpi = [it for it in all_items if isinstance(it, SessionLogModel)]
        _log_week_num = log.week_number or 1
        week_pts_list_kpi = [
            lg.kpi_contribution
            for lg in session_logs_for_kpi
            if lg.kpi_contribution is not None and lg.week_number == _log_week_num
        ]
        logged_slots_kpi = {
            (lg.week_number, lg.day_of_week)
            for lg in session_logs_for_kpi
            if lg.status == "done"
        }
        kpi_block = compute_weekly_kpi_block(
            pts_this_session=log.kpi_contribution,
            weeks_total=plan_schema_kpi.weeks_count,
            week_pts_list=week_pts_list_kpi,
            sessions_done=_sessions_done_week_s,
            sessions_planned=_get_sessions_planned_week(plan, _log_week_num) or 1,
            plan=plan_schema_kpi,
            week_number=_log_week_num,
            logged_slots=logged_slots_kpi,
        )
        pts_weekly_s = int(round(log.kpi_contribution * plan_schema_kpi.weeks_count))

    # VI ignoré si durée < 30 min (géré aussi dans activity_analysis.py, double-sécurité)
    vi = log.variability_index if (log.duration_minutes_actual or 0) >= 30 else None
    asyncio.create_task(_reveal_activity_analysis(
        callback.message,
        kpi_block=kpi_block,
        pts_weekly=pts_weekly_s,
        planned_tss=planned_tss,
        actual_tss=log.tss_actual,
        rpe=rpe_effective,
        duration_minutes=log.duration_minutes_actual,
        planned_duration_minutes=planned_duration,
        user_level=user_level,
        fatigue_anomaly=fatigue_anomaly,
        ctl=metrics.ctl,
        atl=metrics.atl,
        tsb=metrics.tsb,
        session_type_real=log.session_type_real,
        planned_workout_type=planned_workout_type,
        dominant_zone=log.dominant_zone,
        time_in_zones_s=log.time_in_zones_s,
        respect_zones_score=log.respect_zones_score,
        cardiac_drift_index=log.cardiac_drift_index,
        intervals_consistency_index=log.intervals_consistency_index,
        normalized_power=log.normalized_power,
        avg_power=log.avg_power,
        intensity_factor=log.intensity_factor,
        variability_index=vi,
        elevation_gain_m=log.elevation_gain_m,
        average_temp_c=log.average_temp_c,
        is_group_ride=bool(log.athlete_count and log.athlete_count > 1),
        weekly_snapshot=snap,
        sessions_planned_week=_get_sessions_planned_week(plan, log.week_number),
        sessions_done_week=_sessions_done_week_s,
        next_session_info=_get_next_session_info(plan, log.week_number, log.day_of_week),
        storytelling_mode=storytelling_mode,
        highlight_category=highlight_category,
        personal_record=personal_record_dict,
    ))
