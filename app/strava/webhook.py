"""
Gestion du webhook Strava.

Flux :
  POST /auth/strava/webhook  ← Strava envoie un event à chaque activité créée
    ↓
  handle_activity_event() :
    1. Trouve le user via owner_id (= provider_user_id dans oauth_connections)
    2. Vérifie que l'activité n'est pas déjà loggée
    3. Fetch les détails de l'activité depuis l'API Strava
    4. Matche avec la séance planifiée (scoring sémantique)
    5. Sauvegarde session_log (tss calculé via la logique interne)
    6. Envoie un message Telegram avec boutons RPE emoji
"""

import asyncio
import logging
import random
from datetime import date, timedelta

from sqlalchemy import select

from app.db import repositories as repo
from app.db.client import AsyncSessionFactory
from app.db.models.oauth_connection import OAuthConnection
from app.engine.atl_ctl import FitnessMetrics, compute_fitness_from_any, estimate_initial_ctl, tsb_label
from app.engine.adherence_kpi import compute_session_kpi
from app.engine.schemas import TrainingPlanSchema
from app.engine.tss import tss_from_weekly_hours
from app.engine.weekly_snapshot import compute_weekly_snapshot
from app.db.models.user import User
from app.strava.client import ensure_valid_token
from app.strava.fetcher import StravaActivityFetcher, StravaFetchError
from app.strava.analyzer import SessionAnalyzer
from app.strava.analysis_models import AnalyzedSession, RawActivity
from app.strava.matching import evaluate_activity_plan_match
from app.strava.highlight import HighlightResult, build_message_a, build_message_b, select_highlight

logger = logging.getLogger(__name__)

# Correspondance sport_type Strava → on ignore les activités non-cyclisme
_CYCLING_TYPES = {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide", "EBikeRide"}


async def handle_activity_event(event: dict, bot, injected_activity: dict | None = None) -> None:
    """
    Traite un événement Strava de type 'activity create'.
    Appelé depuis le endpoint POST /auth/strava/webhook.
    """
    if event.get("object_type") != "activity" or event.get("aspect_type") != "create":
        return

    strava_activity_id: int = event["object_id"]
    strava_athlete_id: int = event["owner_id"]

    _notify_kwargs: dict | None = None  # rempli à la fin du bloc session
    async with AsyncSessionFactory() as session:
        async with session.begin():
            # 1. Trouver le user via provider_user_id
            result = await session.execute(
                select(OAuthConnection).where(
                    OAuthConnection.provider == "strava",
                    OAuthConnection.provider_user_id == str(strava_athlete_id),
                )
            )
            conn: OAuthConnection | None = result.scalar_one_or_none()
            if conn is None:
                logger.debug(f"Strava athlete {strava_athlete_id} non trouvé en DB — ignoré")
                return

            user_result = await session.execute(
                select(User).where(User.id == conn.user_id)
            )
            user: User | None = user_result.scalar_one_or_none()
            if user is None:
                return

            # 2. Vérifier si déjà loggée
            existing = await repo.session_log_repo.get_by_strava_activity(
                session, strava_activity_id
            )
            if existing is not None:
                logger.debug(f"Activité Strava {strava_activity_id} déjà loggée — ignorée")
                return

            # 3. Récupérer les détails de l'activité
            try:
                access_token = await ensure_valid_token(conn, session)
                fetcher = StravaActivityFetcher()
                raw = await fetcher.fetch_raw_activity(access_token, strava_activity_id)
            except (StravaFetchError, Exception):
                logger.exception(f"Erreur fetch activité Strava {strava_activity_id}")
                return

            # Ignorer les activités non-cyclisme
            sport_type = raw.strava_sport_type or raw.sport_type
            if sport_type not in _CYCLING_TYPES:
                logger.debug(f"Activité {sport_type} ignorée (non-cyclisme)")
                return

            # Calcul TSS / analyse initiale
            profile = await repo.profile_repo.get_by_user_id(session, user.id)
            profile_data = profile.profile if profile else {}
            ftp = profile_data.get("equipment", {}).get("ftp")
            hr_max = profile_data.get("physio", {}).get("hr_max")
            hr_rest = profile_data.get("physio", {}).get("hr_rest")
            sex = profile_data.get("sex")
            threshold_hr = int(hr_max * 0.92) if hr_max else None
            analyzer = SessionAnalyzer()
            analyzed = analyzer.analyze(
                raw,
                ftp=ftp,
                hr_max=hr_max,
                hr_rest=hr_rest,
                threshold_hr=threshold_hr,
                sex=sex,
            )

            # 4. Matcher avec la séance du plan
            plan = await repo.plan_repo.get_active_plan(session, user.id)
            if plan is None:
                logger.debug(f"Pas de plan actif pour user {user.id}")
                return

            activity_date = raw.start_datetime.date()

            # Slots déjà pris cette semaine (séances "done" pour ce plan)
            # Permet d'éviter la double candidature sur le même slot planifié
            all_logs_for_plan = await repo.session_log_repo.get_all_for_user(session, user.id)
            used_slots = frozenset(
                (log.week_number, log.day_of_week)
                for log in all_logs_for_plan
                if str(log.plan_id) == str(plan.id) and log.status == "done"
            )

            match_result = evaluate_activity_plan_match(
                plan, analyzed, activity_date, used_slots=used_slots
            )

            # Métriques communes pour le log
            duration_s = raw.moving_time_s or raw.duration_s
            elapsed_minutes = int(duration_s / 60)
            _has_real_power = raw.has_power
            avg_hr = raw.avg_hr
            avg_w = raw.avg_power if _has_real_power else None
            norm_w = raw.weighted_avg_power if _has_real_power else None
            tss_computed = round(analyzed.tss, 1)
            environment = analyzed.environment or "outdoor"

            # Activités hors-plan : sauvegarder pour qu'elles comptent dans ATL/CTL
            if match_result.candidate is None or not match_result.is_aligned:
                week_num = max(1, (activity_date - plan.start_date).days // 7 + 1)
                dow = activity_date.weekday()
                log_unplanned = await repo.session_log_repo.create(
                    session=session,
                    user_id=user.id,
                    plan_id=plan.id,
                    week_number=week_num,
                    day_of_week=dow,
                    logged_date=activity_date,
                    status="unplanned",
                    duration_minutes_actual=elapsed_minutes,
                    tss_actual=tss_computed or None,
                    strava_activity_id=strava_activity_id,
                    source="strava",
                    avg_heart_rate=int(avg_hr) if avg_hr else None,
                    avg_power=int(avg_w) if avg_w else None,
                    normalized_power=int(norm_w) if norm_w else None,
                    kilojoules=float(raw.kilojoules) if raw.kilojoules else None,
                    environment=environment,
                    time_in_zones_s=analyzed.time_in_zones_s or None,
                    cardiac_drift_index=analyzed.cardiac_drift_index,
                    intervals_consistency_index=analyzed.intervals_consistency_index,
                    respect_zones_score=analyzed.respect_zones_score,
                    session_type_real=analyzed.session_type_real,
                    variability_index=analyzed.variability_index,
                    intensity_factor=analyzed.intensity_factor,
                    dominant_zone=analyzed.dominant_zone,
                    elevation_gain_m=raw.total_elevation_gain,
                    average_temp_c=raw.average_temp,
                    athlete_count=raw.athlete_count if raw.athlete_count > 1 else None,
                )

                fitness_metrics_up, fitness_feedback_up = await _build_fitness_feedback(session, user.id)
                if fitness_metrics_up is not None:
                    log_unplanned.ctl_at_session = round(fitness_metrics_up.ctl, 1)
                    log_unplanned.atl_at_session = round(fitness_metrics_up.atl, 1)
                    log_unplanned.tsb_at_session = round(fitness_metrics_up.tsb, 1)

            if match_result.candidate is None:
                if match_result.all_slots_taken:
                    # Toutes les séances de la fenêtre sont déjà prises → sortie bonus
                    await _notify_bonus_activity(
                        bot, user.telegram_id, raw, tss=tss_computed,
                        fitness_feedback=fitness_feedback_up,
                    )
                else:
                    # Aucune séance planifiée dans la fenêtre
                    await _notify_unplanned(
                        bot, user.telegram_id, raw,
                        reason="Aucune séance planifiée à ±2 jours de cette date.",
                        tss=tss_computed,
                        fitness_feedback=fitness_feedback_up,
                    )
                return

            if not match_result.is_aligned:
                score = match_result.score
                score_label = f"{score.match_level} ({score.confidence_score}/100)" if score else "none"
                await _notify_unplanned(
                    bot, user.telegram_id, raw,
                    reason=f"Activité peu alignée avec la séance prévue (score {score_label}).",
                    details=score.reasons if score else None,
                    tss=tss_computed,
                    fitness_feedback=fitness_feedback_up,
                )
                return

            candidate = match_result.candidate
            session_spec = candidate.session_spec

            # Upgrade Tier 3 pour séance planifiée (streams complets)
            if not raw.is_manual:
                try:
                    raw = await fetcher.ensure_tier3_streams(
                        access_token,
                        strava_activity_id,
                        raw,
                    )
                    analyzed = analyzer.analyze(
                        raw,
                        ftp=ftp,
                        hr_max=hr_max,
                        hr_rest=hr_rest,
                        threshold_hr=threshold_hr,
                        sex=sex,
                        planned_zone=session_spec.zone_code,
                        planned_target_time_in_zone_s=session_spec.target_time_in_zone_minutes,
                    )
                    tss_computed = round(analyzed.tss, 1)
                    norm_w = (raw.weighted_avg_power or analyzed.normalized_power) if _has_real_power else None
                    environment = analyzed.environment or "outdoor"
                except StravaFetchError:
                    logger.warning("Impossible de charger les streams complets Tier 3", exc_info=True)

            # 5. Sauvegarder le log
            log = await repo.session_log_repo.create(
                session=session,
                user_id=user.id,
                plan_id=plan.id,
                week_number=candidate.week_number,
                day_of_week=candidate.day_of_week,
                logged_date=activity_date,
                status="done",
                rpe_emoji=None,
                duration_minutes_actual=elapsed_minutes,
                tss_actual=float(tss_computed) if tss_computed else None,
                strava_activity_id=strava_activity_id,
                source="strava",
                avg_heart_rate=int(avg_hr) if avg_hr else None,
                avg_power=int(avg_w) if avg_w else None,
                normalized_power=int(norm_w) if norm_w else None,
                kilojoules=float(raw.kilojoules) if raw.kilojoules else None,
                environment=environment,
                time_in_zones_s=analyzed.time_in_zones_s or None,
                cardiac_drift_index=analyzed.cardiac_drift_index,
                intervals_consistency_index=analyzed.intervals_consistency_index,
                respect_zones_score=analyzed.respect_zones_score,
                session_type_real=analyzed.session_type_real,
                variability_index=analyzed.variability_index,
                intensity_factor=analyzed.intensity_factor,
                dominant_zone=analyzed.dominant_zone,
                elevation_gain_m=raw.total_elevation_gain,
                average_temp_c=raw.average_temp,
                athlete_count=raw.athlete_count if raw.athlete_count > 1 else None,
            )

            fitness_metrics, fitness_feedback = await _build_fitness_feedback(session, user.id)

            if fitness_metrics is not None:
                log.ctl_at_session = round(fitness_metrics.ctl, 1)
                log.atl_at_session = round(fitness_metrics.atl, 1)
                log.tsb_at_session = round(fitness_metrics.tsb, 1)

            # KPI d'adhérence
            plan_schema = TrainingPlanSchema.model_validate(plan.plan_technical)
            level = profile_data.get("level", "intermediate")
            kpi_week = next(
                (w for w in plan_schema.weeks if w.week_number == candidate.week_number),
                None,
            )
            if kpi_week:
                kpi = compute_session_kpi(
                    tss_planned=session_spec.tss_target,
                    week_tss_planned=kpi_week.total_tss_target,
                    weeks_total=plan_schema.weeks_count,
                    tss_actual=float(tss_computed) if tss_computed else None,
                    workout_type=session_spec.workout_type,
                    session_type_real=analyzed.session_type_real,
                    tsb_after=fitness_metrics.tsb if fitness_metrics else None,
                    level=level,
                )
                log.kpi_contribution = kpi.pts

            # 6. Préparer la notification stagée
            session_logs_snap = await repo.session_log_repo.get_all_for_user(session, user.id)
            strava_activities_snap = await repo.activity_repo.get_for_user(session, user.id, days=42)
            logs_for_snap = session_logs_snap + strava_activities_snap
            weekly_snap = compute_weekly_snapshot(logs_for_snap, activity_date)
            _fm = fitness_metrics or FitnessMetrics(ctl=0.0, atl=0.0, tsb=0.0)
            highlight = select_highlight(analyzed, _fm, weekly_snap)

            _notify_kwargs = dict(
                telegram_id=user.telegram_id,
                raw=raw,
                analyzed=analyzed,
                session_spec=session_spec,
                log_id=log.id,
                match_level=match_result.score.match_level,
                confidence_score=match_result.score.confidence_score,
                day_shift=candidate.day_shift,
                fitness_feedback=fitness_feedback or "",
                fitness_metrics=_fm,
                highlight=highlight,
            )

    # Hors du bloc session — déclenche la notification en arrière-plan
    if _notify_kwargs is not None:
        asyncio.create_task(_notify_staged_rpe_request(bot, **_notify_kwargs))


def _build_rpe_prompt(session_type_real: str | None, tsb: float) -> str:
    """Retourne une question RPE contextuelle (tirage aléatoire dans un pool)."""
    if tsb <= -25:
        return "Comment tu te sens physiquement là ?"
    if tsb >= 10:
        return "Tu as senti la forme aujourd'hui ?"

    pools: dict[str, list[str]] = {
        "intervals": [
            "Tes jambes ont tenu la cadence ?",
            "Fatigue musculaire ou cardio en fin de séance ?",
            "Le dernier intervalle, tu l'as senti comment ?",
        ],
        "long_ride": [
            "Jambes encore fraîches à l'arrivée, ou en mode survie ? 😅",
            "Comment tu te sens là, maintenant ?",
        ],
        "recovery": [
            "Vraiment récupération, ou ça tirait un peu ?",
            "Tu as pu rester facile du début à la fin ?",
        ],
        "endurance": [
            "Ça coulait tout seul ou il fallait pousser ?",
            "Comment c'était ?",
        ],
        "race": [
            "Tout ce que tu avais, tu l'as mis là-dedans ?",
            "Comment tu te sens après l'effort ?",
        ],
        "tempo": [
            "Les jambes ont répondu jusqu'au bout ?",
            "Tu as maintenu l'intensité voulue ?",
        ],
    }
    pool = pools.get(session_type_real or "", ["Comment c'était ?", "Ton ressenti ?", "Physiquement, ça allait ?"])
    return random.choice(pool)


async def _notify_staged_rpe_request(
    bot,
    *,
    telegram_id: int,
    raw: RawActivity,
    analyzed: AnalyzedSession,
    session_spec,
    log_id,
    match_level: str,
    confidence_score: int | None = None,
    day_shift: int = 0,
    fitness_feedback: str = "",
    fitness_metrics: FitnessMetrics,
    highlight: HighlightResult,
) -> None:
    """Envoie la notification post-ride en 3 messages stagés (Variable Reward).

    Message A (silencieux) : accroche / teaser
    Message B (silencieux) : métrique héros
    Message C (notification) : carte visuelle séance + clavier RPE  ← seule vibration
    """
    from app.bot.keyboards.session_log import rpe_emoji_keyboard_strava
    from app.strava.highlight import build_message_c_session_card

    try:
        # ── Message A — Teaser (silencieux) ──────────────────────────────────
        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(1.2)
        await bot.send_message(
            telegram_id,
            build_message_a(highlight),
            parse_mode="HTML",
            disable_notification=True,
        )

        # ── Message B — Métrique héros (silencieux) ───────────────────────────
        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(0.8)
        await bot.send_message(
            telegram_id,
            build_message_b(highlight, analyzed, pr=None),
            parse_mode="HTML",
            disable_notification=True,
        )

        # ── Message C — Carte visuelle séance + clavier RPE (notification) ───
        await bot.send_chat_action(telegram_id, "typing")
        await asyncio.sleep(1.0)

        rpe_prompt = _build_rpe_prompt(analyzed.session_type_real, fitness_metrics.tsb)
        kb = rpe_emoji_keyboard_strava(str(log_id))
        card = build_message_c_session_card(
            analyzed=analyzed,
            session_spec=session_spec,
            rpe_prompt=rpe_prompt,
            match_level=match_level,
            confidence_score=confidence_score,
            day_shift=day_shift,
        )
        await bot.send_message(telegram_id, card, parse_mode="HTML", reply_markup=kb)
    except Exception:
        logger.exception("Erreur lors de l'envoi de la notification stagée RPE")


async def _notify_bonus_activity(
    bot,
    telegram_id: int,
    raw: RawActivity,
    tss: float | None = None,
    fitness_feedback: str = "",
) -> None:
    """Notifie une activité bonus (séance planifiée déjà comptabilisée ce jour)."""
    elapsed_min = int(raw.duration_s / 60)
    name = raw.name or "Activité"
    tss_text = f" · ~{tss:.0f} TSS" if tss else ""
    fitness_text = f"\n\n{fitness_feedback}" if fitness_feedback else ""

    await bot.send_message(
        telegram_id,
        f"🔄 <b>Sortie bonus enregistrée</b>\n\n"
        f"{name} — {elapsed_min} min{tss_text}\n\n"
        f"Ta séance planifiée ce jour est déjà comptabilisée. "
        f"Cette sortie s'ajoute à ta charge hebdomadaire."
        f"{fitness_text}",
        parse_mode="HTML",
    )


async def _notify_unplanned(
    bot,
    telegram_id: int,
    raw: RawActivity,
    reason: str,
    details: list[str] | None = None,
    tss: float | None = None,
    fitness_feedback: str = "",
) -> None:
    """Notifie l'utilisateur d'une activité hors plan (sauvegardée en DB)."""
    elapsed_min = int(raw.duration_s / 60)
    name = raw.name or "Activité"
    details_text = ""
    if details:
        details_text = "\n" + "\n".join(f"• {detail}" for detail in details)
    tss_text = f" · ~{tss:.0f} TSS" if tss else ""
    fitness_text = f"\n\n{fitness_feedback}" if fitness_feedback else ""

    await bot.send_message(
        telegram_id,
        f"🚴 <b>Activité hors plan détectée</b>\n\n"
        f"{name} — {elapsed_min} min{tss_text}\n\n"
        f"{reason}{details_text}"
        f"{fitness_text}",
        parse_mode="HTML",
    )


async def _build_fitness_feedback(session, user_id) -> tuple[FitnessMetrics | None, str]:
    """Calcule ATL/CTL/TSB et retourne (metrics, texte_feedback)."""
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    plan_start = plan.start_date if plan else date.today()

    activities = await repo.activity_repo.get_for_user(session, user_id, days=365)
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]
    logs = await repo.session_log_repo.get_all_for_user(session, user_id)

    all_items = pre_plan_acts + logs
    if not all_items:
        return None, "📊 Données de forme indisponibles pour le moment."

    initial_ctl = await _estimate_ctl_seed(all_items, session, user_id)
    seed_date = (date.today() - timedelta(days=49)) if initial_ctl > 0 else None
    metrics = compute_fitness_from_any(all_items, initial_ctl=initial_ctl, seed_date=seed_date)
    label = tsb_label(metrics.tsb)
    text = (
        "📊 <b>Impact forme après cette séance</b>\n"
        f"CTL {metrics.ctl:.0f} · ATL {metrics.atl:.0f} · TSB {metrics.tsb:+.0f}\n"
        f"{label}"
    )
    return metrics, text


def _item_date(it):
    return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)


async def _estimate_ctl_seed(items: list, session, user_id) -> float:
    """Amorce CTL si l'historique visible est trop court (<84 jours)."""
    if not items:
        return 0.0

    oldest = min(_item_date(it) for it in items)
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
