"""
Router : /forme — affichage ATL/CTL/TSB avec interprétation LLM.

Sources de données (sans double-comptage) :
  - activities avec activity_date < plan.start_date → historique pré-plan (intervals.icu)
  - session_logs → exécution du plan en cours
"""

import logging
from datetime import date, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.text_format import to_telegram_html
from app.config import settings
from app.core.localization import t
from app.db import repositories as repo
from app.db.models.user import User
from app.engine.atl_ctl import compute_fitness_from_any, estimate_initial_ctl, tsb_label
from app.engine.power_curve import (
    PowerCurveDelta,
    SustainabilityProfile,
    compute_power_curve_delta,
    compute_sustainability_profile,
)
from app.engine.rpe import rpe_emoji as _rpe_emoji_for
from app.engine.schemas import AthleteProfileSchema
from app.engine.tss import tss_from_weekly_hours
from app.providers.intervals.client import IntervalsClient
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)
router = Router()

_DELTA_ANCHOR_LABELS = ("5s", "60s", "300s", "1200s", "3600s")
_SUSTAINABILITY_KEY_ANCHORS = (("1200s", "20min"), ("3600s", "60min"))


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


def _item_date(it):
    return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)


def _item_tss(it):
    return getattr(it, "tss_actual", None) or getattr(it, "tss", None)


def _item_icon(it):
    if hasattr(it, "rpe"):  # SessionLog
        return _rpe_emoji_for(it.rpe)
    sport = getattr(it, "sport_type", "")
    return "🏋️" if sport == "VirtualRide" else "🚴"  # Activity


@router.message(Command("forme", "fitness"))
async def cmd_forme(message: Message, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer(t("forme.onboarding_first"))
        return

    # Frontière = date de début du plan actif
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    plan_start = plan.start_date if plan else date.today()

    # Historique pré-plan (évite le double-comptage avec session_logs)
    activities = await repo.activity_repo.get_for_user(session, user.id, days=365)
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]

    # Exécution du plan (session_logs)
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)

    all_items = pre_plan_acts + logs

    if not all_items:
        await message.answer(t("forme.no_data"))
        return

    # Consommée depuis la source (spec 002 FR-016, app/services/fitness.py) — recalcul
    # local uniquement en repli si aucun wellness n'a encore été ingéré (nouvel athlète
    # avant le premier import/poll)
    current = await get_current_fitness(session, user.id)
    if current is not None:
        metrics = current.metrics
        staleness_note = (
            t("forme.stale_note", date=current.as_of.strftime("%d/%m"))
            if current.is_stale else ""
        )
    else:
        initial_ctl = await _estimate_ctl_seed(all_items, session, user.id)
        seed_date = (date.today() - timedelta(days=49)) if initial_ctl > 0 else None
        metrics = compute_fitness_from_any(all_items, initial_ctl=initial_ctl, seed_date=seed_date)
        staleness_note = t("forme.local_estimate_note")
    label = tsb_label(metrics.tsb)

    # Tableau des 7 derniers items
    recent = sorted(all_items, key=_item_date, reverse=True)[:7]
    history_lines = []
    for it in reversed(recent):
        tss_val = _item_tss(it)
        tss_str = f"{tss_val:.0f}" if tss_val else "—"
        icon = _item_icon(it)
        d = _item_date(it)
        history_lines.append(f"  {d.strftime('%d/%m')} · {icon} · TSS {tss_str}")
    history_text = "\n".join(history_lines)

    metrics_text = (
        f"{t('forme.title')}\n\n"
        f"{staleness_note}"
        f"{t('forme.ctl_label')} : <b>{metrics.ctl:.0f}</b>\n"
        f"{t('forme.atl_label')} : <b>{metrics.atl:.0f}</b>\n"
        f"{t('forme.tsb_label')}   : <b>{metrics.tsb:+.0f}</b>  {label}\n\n"
        f"{t('forme.recent_sessions_header')}\n{history_text}\n\n"
        f"{t('forme.analysis_in_progress')}"
    )
    await message.answer(metrics_text, parse_mode="HTML")

    # Interprétation LLM
    profile_db = await repo.profile_repo.get_by_user_id(session, user.id)
    user_level: int = (profile_db.profile or {}).get("user_level", 0) if profile_db else 0
    interpretation = await _generate_fitness_interpretation(metrics, logs, pre_plan_acts, recent, user_level=user_level)
    await message.answer(to_telegram_html(interpretation), parse_mode="HTML")

    # Profil de puissance (power-curve delta + sustainability_profile, 2026-09-21) —
    # best-effort, message séparé, silencieux si indisponible (pas de FTP, endpoint en
    # échec, pas assez de données).
    profile_schema: AthleteProfileSchema | None = None
    if profile_db and profile_db.profile:
        try:
            profile_schema = AthleteProfileSchema.model_validate(profile_db.profile)
        except Exception:
            logger.warning("Impossible de valider le profil athlète pour /forme")
    power_profile_text = await _fetch_power_profile(profile_schema)
    if power_profile_text:
        await message.answer(power_profile_text, parse_mode="HTML")


async def _generate_fitness_interpretation(
    metrics, logs: list, activities: list, recent_items: list, user_level: int = 0
) -> str:
    """Génère une interprétation LLM de la forme actuelle. Fallback déterministe."""
    try:
        from app.llm.factory import get_provider
        provider = get_provider()

        done_count = sum(1 for l in logs if l.status == "done")
        skipped_count = sum(1 for l in logs if l.status == "skipped")
        pre_plan_count = len(activities)

        # Contexte source des données — adapté selon la situation
        if done_count == 0 and pre_plan_count > 0:
            context_line = t("forme.llm_context_plan_start", count=pre_plan_count)
        elif done_count > 0:
            context_line = t("forme.llm_context_adherence", done=done_count)
            if skipped_count:
                context_line += t("forme.llm_context_adherence_skipped", count=skipped_count)
            context_line += "."
        else:
            context_line = t("forme.llm_context_no_data")

        # Séances récentes (date + TSS) — exactement ce qui est affiché à l'athlète
        recent_lines = []
        for it in reversed(recent_items):  # ordre chronologique
            d = getattr(it, "logged_date", None) or getattr(it, "activity_date", None)
            tss = getattr(it, "tss_actual", None) or getattr(it, "tss", None)
            if d and tss:
                recent_lines.append(
                    t("forme.llm_recent_line", date=d.strftime("%d/%m"), tss=f"{tss:.0f}")
                )
        recent_text = "\n".join(recent_lines) if recent_lines else t("forme.llm_recent_none")

        from app.llm.prompts import build_ux_system_prompt

        prompt = t(
            "forme.llm_prompt",
            ctl=f"{metrics.ctl:.0f}",
            atl=f"{metrics.atl:.0f}",
            tsb=f"{metrics.tsb:+.0f}",
            context_line=context_line,
            recent_text=recent_text,
        )

        return await provider.generate(
            system_prompt=build_ux_system_prompt(user_level),
            user_message=prompt,
            max_tokens=4000,
        )

    except Exception as e:
        logger.warning(f"Erreur LLM /forme : {e} — fallback")
        return _fallback_interpretation(metrics)


def _fallback_interpretation(metrics) -> str:
    tsb = metrics.tsb
    if tsb < -30:
        return t("forme.fallback_critical")
    if tsb < 0:
        return t("forme.fallback_load")
    if tsb <= 5:
        return t("forme.fallback_balanced")
    if tsb <= 15:
        return t("forme.fallback_peak")
    if tsb <= 20:
        return t("forme.fallback_fresh")
    return t("forme.fallback_overreached")


async def _fetch_power_profile(profile: AthleteProfileSchema | None) -> str | None:
    """Best-effort, calculé à la demande pour /forme, pas persisté (CLAUDE.md § Power-
    curve delta) — 4 appels réseau intervals.icu (power-curves ×3, wellness du jour pour
    W'). `None` si indisponible (endpoint en échec, pas assez de données, pas de FTP
    déclaré) — jamais affiché comme si la donnée existait."""
    if profile is None or not profile.equipment.ftp:
        return None
    try:
        client = _client()
        today = date.today()
        w1_start = (today - timedelta(days=27)).isoformat()
        w1_end = today.isoformat()
        w2_end = (today - timedelta(days=28)).isoformat()
        w2_start = (today - timedelta(days=55)).isoformat()
        cur_id = f"r.{w1_start}.{w1_end}"
        prev_id = f"r.{w2_start}.{w2_end}"

        power_response = await client.get_power_curves(
            curve_type="power",
            windows=[(w1_start, w1_end), (w2_start, w2_end)],
            activity_type="Ride",
        )
        delta = compute_power_curve_delta(
            power_response, current_window_id=cur_id, previous_window_id=prev_id
        )

        sus_start = (today - timedelta(days=41)).isoformat()
        sus_end = today.isoformat()
        sus_id = f"r.{sus_start}.{sus_end}"
        ride_response = await client.get_power_curves(
            curve_type="power", windows=[(sus_start, sus_end)], activity_type="Ride"
        )
        vride_response = await client.get_power_curves(
            curve_type="power", windows=[(sus_start, sus_end)], activity_type="VirtualRide"
        )

        # W' (joules) — pas encore stocké localement, lu depuis le wellness du jour
        # (sportInfo[].wPrime, même chemin que Section11 — voir CLAUDE.md).
        w_prime = None
        wellness_today = await client.list_wellness(oldest=sus_end, newest=sus_end)
        if wellness_today:
            cycling_info = next(
                (s for s in (wellness_today[0].get("sportInfo") or []) if s.get("type") == "Ride"),
                None,
            )
            if cycling_info:
                w_prime = cycling_info.get("wPrime")

        sustainability = compute_sustainability_profile(
            [ride_response, vride_response],
            window_id=sus_id,
            ftp=float(profile.equipment.ftp),
            w_prime=w_prime,
            weight_kg=profile.weight_kg,
        )
        return _format_power_profile(delta, sustainability)
    except Exception:
        logger.warning("Impossible de calculer le profil de puissance pour /forme")
        return None


def _format_power_profile(
    delta: PowerCurveDelta, sustainability: SustainabilityProfile
) -> str | None:
    blocks: list[str] = []

    if delta.rotation_index is not None:
        parts = []
        for label in _DELTA_ANCHOR_LABELS:
            anchor = delta.anchors.get(label)
            if anchor and anchor.pct_change is not None:
                sign = "+" if anchor.pct_change >= 0 else ""
                parts.append(f"{label} {sign}{anchor.pct_change:.0f}%")
        if parts:
            bias = (
                t("forme.power_profile_bias_sprint") if delta.rotation_index > 0
                else t("forme.power_profile_bias_endurance")
            )
            blocks.append(
                t("forme.power_profile_header") + "\n"
                + " | ".join(parts)
                + "\n"
                + t(
                    "forme.power_profile_rotation",
                    bias=bias,
                    rotation=f"{delta.rotation_index:+.1f}",
                )
            )

    if sustainability.note is None:
        sus_parts = []
        for key, label in _SUSTAINABILITY_KEY_ANCHORS:
            anchor = sustainability.anchors.get(key)
            if anchor and anchor.actual_watts is not None:
                div = (
                    t("forme.sustainability_divergence", pct=f"{anchor.model_divergence_pct:+.0f}")
                    if anchor.model_divergence_pct is not None else ""
                )
                sus_parts.append(f"{label} : {anchor.actual_watts:.0f}W{div}")
        if sus_parts:
            blocks.append(t("forme.sustainability_header") + "\n" + " | ".join(sus_parts))

    return "\n\n".join(blocks) if blocks else None


async def _estimate_ctl_seed(items: list, session, user_id) -> float:
    """
    Retourne un CTL initial (seed) si la fenêtre de données est < 84j (2×τ_CTL).

    En dessous de 84j, l'EMA CTL n'a pas convergé depuis 0 et sous-estime la vraie
    fitness. On amorce avec le TSS moyen journalier estimé depuis le profil athlète
    (hours_per_week → tss_from_weekly_hours / 7).

    Si fenêtre ≥ 84j ou profil indisponible, retourne 0.0 (comportement EMA standard).
    """
    if not items:
        return 0.0

    oldest = min(_item_date(it) for it in items)
    span_days = (date.today() - oldest).days
    if span_days >= 84:
        return 0.0  # EMA a convergé — pas d'amorçage nécessaire

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
