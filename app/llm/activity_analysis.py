"""
Génère une analyse LLM d'une activité en comparant réalisé vs prévu.
Appelé depuis session_log.py après la capture du RPE.

Principe : les calculs de match score, détection RPE mismatch, et interprétation
des métriques sont déterministes — le LLM ne fait qu'interpréter les valeurs
pré-calculées, organisées en blocs thématiques.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.localization import t
from app.engine.rpe import RPE_EASY_MAX, RPE_HARD_MIN, rpe_label

if TYPE_CHECKING:
    from app.engine.weekly_snapshot import WeeklySnapshot


def _compute_match_score(
    planned: float | None,
    actual: float | None,
) -> tuple[str, str]:
    """Calcule l'écart TSS prévu/réalisé. Retourne (emoji_flag, description).

    Seuils : ±15% = ✅ | ±30% = ⚠️ | >30% = 📊
    """
    if planned is None or actual is None or planned == 0:
        return ("", "")

    pct_diff = (actual / planned - 1) * 100

    if abs(pct_diff) <= 15:
        flag = "✅"
    elif abs(pct_diff) <= 30:
        flag = "⚠️"
    else:
        flag = "📊"

    sign = "+" if pct_diff >= 0 else ""
    return (flag, t(
        "llm.match_score.text", actual=f"{actual:.0f}", planned=f"{planned:.0f}",
        sign=sign, pct=f"{pct_diff:.0f}",
    ))


def _detect_rpe_mismatch(
    rpe: float | None,
    actual_tss: float | None,
    planned_tss: float | None,
    actual_duration_minutes: int | None = None,
    planned_duration_minutes: int | None = None,
) -> str | None:
    """Détecte les incohérences entre RPE et TSS. Retourne un message d'alerte ou None."""
    if rpe is None:
        return t("llm.rpe_mismatch.no_rpe")

    if actual_tss is not None and planned_tss is not None and planned_tss > 0:
        ratio = actual_tss / planned_tss
        if rpe >= RPE_HARD_MIN and ratio < 0.85:
            return t("llm.rpe_mismatch.hard_but_low_tss")
        if rpe <= RPE_EASY_MAX and ratio > 1.15:
            # Pas d'alerte si le TSS élevé s'explique par une durée plus longue
            if (
                actual_duration_minutes
                and planned_duration_minutes
                and planned_duration_minutes > 0
            ):
                duration_ratio = actual_duration_minutes / planned_duration_minutes
                if ratio / duration_ratio <= 1.15:
                    return None
            return t("llm.rpe_mismatch.easy_but_high_tss")

    return None


def _tsb_tone(tsb: float | None) -> str:
    """Directive tonalité alignée sur les seuils de tsb_label()."""
    if tsb is None:
        return t("llm.tsb_tone.balanced")
    if tsb < -30:
        return t("llm.tsb_tone.protective")
    if tsb >= 5:
        return t("llm.tsb_tone.motivating")
    return t("llm.tsb_tone.balanced_pedagogical")


async def generate_activity_analysis(
    *,
    # Existants
    planned_tss: float | None,
    actual_tss: float | None,
    rpe: float | None,           # échelle standard 1-10 (app/engine/rpe.py) | None
    duration_minutes: int | None,
    planned_duration_minutes: int | None = None,
    user_level: int = 0,
    # Anomalie fatigue (HRSS scalaire au moment du RPE)
    fatigue_anomaly: dict | None = None,
    # PMC (ATL/CTL/TSB)
    ctl: float | None = None,
    atl: float | None = None,
    tsb: float | None = None,
    # Métriques qualité
    session_type_real: str | None = None,
    planned_workout_type: str | None = None,
    dominant_zone: str | None = None,
    time_in_zones_s: dict | None = None,
    respect_zones_score: float | None = None,
    cardiac_drift_index: float | None = None,
    intervals_consistency_index: float | None = None,
    # Puissance
    normalized_power: int | None = None,
    avg_power: int | None = None,
    intensity_factor: float | None = None,
    variability_index: float | None = None,  # ignoré si duration_minutes < 30
    # Contexte extérieur
    elevation_gain_m: float | None = None,
    average_temp_c: float | None = None,
    is_group_ride: bool = False,
    # Charge hebdomadaire
    weekly_snapshot: WeeklySnapshot | None = None,
    sessions_planned_week: int | None = None,
    sessions_done_week: int | None = None,  # séances réalisées semaine calendaire (lundi→auj) — prioritaire sur sessions_done_7d
    next_session_info: str | None = None,   # prochaine séance planifiée (ex: "Jeudi — Intervalles Z4, 75 min")
    # Variable Reward — analyse narrative (optionnel)
    highlight_category: str | None = None,   # catégorie mise en avant dans le Message B
    personal_record: dict | None = None,     # PersonalRecord sérialisé ou None
    storytelling_mode: str | None = None,    # "journalist" | "analyst" | "coach"
) -> str:
    """Génère une analyse LLM de l'activité (4-5 phrases, vocabulaire adapté au level).

    Les calculs de match/mismatch sont déterministes et passés comme contexte au LLM.
    Le LLM interprète uniquement — il ne recalcule pas.

    Returns:
        Texte d'analyse (4-5 phrases) — fallback déterministe si LLM indisponible.
    """
    from app.llm.factory import get_provider
    from app.llm.prompts import build_narrative_system_prompt, build_ux_system_prompt

    match_flag, match_text = _compute_match_score(planned_tss, actual_tss)
    # FatigueAnomaly (HRSS scalaire) prend la priorité sur le fallback TSS/plan
    rpe_hint = (
        fatigue_anomaly.get("message")
        if fatigue_anomaly
        else _detect_rpe_mismatch(rpe, actual_tss, planned_tss, duration_minutes, planned_duration_minutes)
    )

    lines = [t("llm.activity_analysis.header")]

    # ── Contexte Variable Reward (injecté en tête si fourni) ──────────────────
    if highlight_category:
        lines.append(t("llm.activity_analysis.highlight_metric", category=highlight_category))
    if personal_record:
        lines.append(t(
            "llm.activity_analysis.personal_record",
            label=personal_record.get("label_fr", ""),
            value=personal_record.get("value", ""),
            previous_best=personal_record.get("previous_best", ""),
            sessions_compared=personal_record.get("sessions_compared", ""),
        ))

    # ── Bloc 1 : Séance ──────────────────────────────────────────────────────
    lines.append(t("llm.activity_analysis.session_header"))
    if session_type_real:
        type_label = session_type_real.replace("_", " ").capitalize()
        if planned_workout_type:
            planned_label = planned_workout_type.replace("_", " ").capitalize()
            lines.append(t(
                "llm.activity_analysis.type_planned_actual", planned=planned_label, actual=type_label
            ))
        else:
            lines.append(t("llm.activity_analysis.type_detected", type=type_label))
    elif planned_workout_type:
        lines.append(t(
            "llm.activity_analysis.type_planned_only",
            type=planned_workout_type.replace("_", " ").capitalize(),
        ))

    if duration_minutes:
        if planned_duration_minutes:
            lines.append(t(
                "llm.activity_analysis.duration_with_planned",
                actual=duration_minutes, planned=planned_duration_minutes,
            ))
        else:
            lines.append(t("llm.activity_analysis.duration", minutes=duration_minutes))
    if actual_tss is not None:
        lines.append(t("llm.activity_analysis.tss_actual", tss=f"{actual_tss:.0f}"))

    if match_text:
        lines.append(t("llm.activity_analysis.plan_comparison", flag=match_flag, text=match_text))
    elif planned_tss is None:
        lines.append(t("llm.activity_analysis.no_planned_session"))

    if rpe is not None:
        lines.append(t("llm.activity_analysis.rpe_line", label=rpe_label(rpe)))
        if fatigue_anomaly:
            lines.append(t(
                "llm.activity_analysis.rpe_cardiac_estimate",
                value=fatigue_anomaly["rpe_cardiac_estimate"], delta=fatigue_anomaly["rpe_delta"],
            ))
    else:
        lines.append(t("llm.activity_analysis.rpe_missing"))

    if rpe_hint:
        lines.append(t("llm.activity_analysis.fatigue_alert", hint=rpe_hint))

    # ── Bloc 2 : Puissance ───────────────────────────────────────────────────
    power_lines: list[str] = []
    if normalized_power:
        power_lines.append(t("llm.activity_analysis.np_line", watts=normalized_power))

    if intensity_factor is not None:
        if intensity_factor >= 0.95:
            if_note = t("llm.activity_analysis.if_note_max")
        elif intensity_factor >= 0.85:
            if_note = t("llm.activity_analysis.if_note_threshold")
        elif intensity_factor >= 0.75:
            if_note = t("llm.activity_analysis.if_note_sweet_spot")
        else:
            if_note = t("llm.activity_analysis.if_note_endurance")
        power_lines.append(t("llm.activity_analysis.if_line", value=f"{intensity_factor:.2f}", note=if_note))

    # VI ignoré si durée < 30 min (pas représentatif sur courtes sorties)
    if variability_index is not None and (duration_minutes or 0) >= 30:
        vi_note = (
            t("llm.activity_analysis.vi_note_steady") if variability_index < 1.05
            else t("llm.activity_analysis.vi_note_variable")
        )
        power_lines.append(t("llm.activity_analysis.vi_line", value=f"{variability_index:.2f}", note=vi_note))

    if power_lines:
        lines.append(t("llm.activity_analysis.power_header"))
        lines.extend(power_lines)

    # ── Bloc 3 : Qualité ─────────────────────────────────────────────────────
    quality_lines: list[str] = []
    if dominant_zone:
        quality_lines.append(t("llm.activity_analysis.dominant_zone", zone=dominant_zone))

    if time_in_zones_s:
        zone_detail = " / ".join(
            f"{z}={int(s/60)}min"
            for z, s in sorted(time_in_zones_s.items())
            if s > 0
        )
        quality_lines.append(t("llm.activity_analysis.zone_distribution", detail=zone_detail))

    if respect_zones_score is not None:
        rz_label = (
            t("llm.activity_analysis.zone_respected") if respect_zones_score >= 80
            else t("llm.activity_analysis.zone_to_improve")
        )
        quality_lines.append(t(
            "llm.activity_analysis.zone_respect", score=f"{respect_zones_score:.0f}", label=rz_label
        ))

    if cardiac_drift_index is not None:
        drift_pct = cardiac_drift_index * 100
        drift_note = (
            t("llm.activity_analysis.drift_ok") if abs(drift_pct) < 10
            else t("llm.activity_analysis.drift_notable")
        )
        quality_lines.append(t("llm.activity_analysis.drift", pct=f"{drift_pct:+.1f}", note=drift_note))

    if intervals_consistency_index is not None:
        ci_pct = round(intervals_consistency_index * 100)
        ci_note = (
            t("llm.activity_analysis.consistency_regular") if ci_pct >= 80
            else t("llm.activity_analysis.consistency_irregular")
        )
        quality_lines.append(t("llm.activity_analysis.consistency", pct=ci_pct, note=ci_note))

    if quality_lines:
        lines.append(t("llm.activity_analysis.quality_header"))
        lines.extend(quality_lines)

    # ── Bloc 4 : Contexte extérieur ──────────────────────────────────────────
    context_lines: list[str] = []
    if elevation_gain_m:
        context_lines.append(t("llm.activity_analysis.elevation", meters=f"{elevation_gain_m:.0f}"))
    if average_temp_c is not None:
        context_lines.append(t("llm.activity_analysis.temperature", temp=f"{average_temp_c:.0f}"))
    if is_group_ride:
        context_lines.append(t("llm.activity_analysis.group_ride"))

    if context_lines:
        lines.append(t("llm.activity_analysis.context_header"))
        lines.extend(context_lines)

    # ── Bloc 5 : Forme & charge hebdomadaire ─────────────────────────────────
    pmc_lines: list[str] = []
    if ctl is not None and atl is not None and tsb is not None:
        pmc_lines.append(t(
            "llm.activity_analysis.pmc_metrics", ctl=f"{ctl:.0f}", atl=f"{atl:.0f}", tsb=f"{tsb:+.0f}"
        ))
        pmc_lines.append(t("llm.activity_analysis.tone_directive", tone=_tsb_tone(tsb)))

    if weekly_snapshot is not None:
        snap = weekly_snapshot
        if snap.tss_6w_avg > 0:
            pmc_lines.append(t(
                "llm.activity_analysis.load_7d_with_trend",
                tss=f"{snap.tss_7d:.0f}", trend=f"{snap.load_trend_pct:+.0f}", avg6w=f"{snap.tss_6w_avg:.0f}",
            ))
        else:
            pmc_lines.append(t("llm.activity_analysis.load_7d_no_history", tss=f"{snap.tss_7d:.0f}"))
        done_count = sessions_done_week if sessions_done_week is not None else snap.sessions_done_7d
        if sessions_planned_week is not None:
            remaining = max(0, sessions_planned_week - done_count)
            pmc_lines.append(t(
                "llm.activity_analysis.sessions_week_with_target",
                done=done_count, planned=sessions_planned_week, remaining=remaining,
            ))
        else:
            pmc_lines.append(t("llm.activity_analysis.sessions_week_no_target", done=done_count))
        if next_session_info:
            pmc_lines.append(t("llm.activity_analysis.next_session", info=next_session_info))
        if snap.monotony_index is not None:
            from app.engine.guardrail_thresholds import MONOTONY_HIGH

            mono_note = (
                t("llm.activity_analysis.monotony_note") if snap.monotony_index > MONOTONY_HIGH
                else t("llm.activity_analysis.variety_note")
            )
            pmc_lines.append(t(
                "llm.activity_analysis.monotony_line",
                value=f"{snap.monotony_index:.1f}", note=mono_note, threshold=f"{MONOTONY_HIGH:.1f}",
            ))

    if pmc_lines:
        lines.append(t("llm.activity_analysis.form_header"))
        lines.extend(pmc_lines)

    # ── Instruction de structure ──────────────────────────────────────────────
    # En mode narratif, la structure est définie dans le system prompt (build_narrative_system_prompt).
    # En mode classique, on conserve la structure explicite ci-dessous.
    if not storytelling_mode:
        lines.append(t("llm.activity_analysis.response_structure"))

    user_message = "\n".join(lines)

    if storytelling_mode:
        system_prompt = build_narrative_system_prompt(user_level, storytelling_mode)
    else:
        system_prompt = build_ux_system_prompt(user_level)

    try:
        provider = get_provider()
        return await provider.generate(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=2900,
        )
    except Exception as exc:
        # Fallback déterministe si LLM indisponible
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[activity_analysis] LLM indisponible — fallback déterministe. Erreur : %s", exc
        )
        parts = []
        if personal_record:
            parts.append(t("llm.activity_analysis.fallback_pr", label=personal_record.get("label_fr", "")))
        if match_text:
            parts.append(t("llm.activity_analysis.fallback_match", flag=match_flag, text=match_text))
        if rpe_hint:
            parts.append(rpe_hint)
        elif rpe is not None:
            parts.append(t("llm.activity_analysis.fallback_rpe", label=rpe_label(rpe)))
        if tsb is not None:
            parts.append(t("llm.activity_analysis.fallback_tsb", tsb=f"{tsb:+.0f}"))
        return " ".join(parts) if parts else t("llm.activity_analysis.fallback_default")


async def generate_coach_blocks(
    *,
    tsb: float | None = None,
    tsb_label_str: str | None = None,
    load_trend_pct: float | None = None,
    tss_6w_daily_avg: float | None = None,
    sessions_done_week: int | None = None,
    sessions_planned_week: int | None = None,
    tss_actual: float | None = None,
    tss_planned: float | None = None,
    session_type_real: str | None = None,
    planned_workout_type: str | None = None,
    dominant_zone: str | None = None,
    time_in_zones_pct: dict | None = None,
    rpe: float | None = None,
    next_session_info: str | None = None,
    coaching_mode: str = "goal",
    user_level: int = 0,
) -> dict[str, str]:
    """Retourne {"form_interpretation", "session_interpretation", "next_advice"}.

    Le LLM interprète les données en langage simple — ne calcule rien.
    Fallback déterministe si LLM indisponible ou JSON invalide.
    """
    import json as _json
    import logging as _logging

    from app.core.localization import t
    from app.llm.factory import get_provider
    from app.llm.prompts import build_coach_blocks_user_message, coach_blocks_system_prompt

    _log = _logging.getLogger(__name__)

    def _fallback() -> dict[str, str]:
        if coaching_mode == "freestyle":
            return {
                "form_interpretation": tsb_label_str or t("llm.coach_blocks.fallback_form"),
                "session_interpretation": t("llm.coach_blocks.fallback_session_freestyle"),
                "next_advice": t("llm.coach_blocks.fallback_advice_freestyle"),
            }
        return {
            "form_interpretation": tsb_label_str or t("llm.coach_blocks.fallback_form"),
            "session_interpretation": t("llm.coach_blocks.fallback_session_goal"),
            "next_advice": next_session_info or t("llm.coach_blocks.fallback_advice_goal"),
        }

    user_message = build_coach_blocks_user_message(
        tsb=tsb,
        tsb_label_str=tsb_label_str,
        load_trend_pct=load_trend_pct,
        tss_6w_daily_avg=tss_6w_daily_avg,
        sessions_done_week=sessions_done_week,
        sessions_planned_week=sessions_planned_week,
        tss_actual=tss_actual,
        tss_planned=tss_planned,
        session_type_real=session_type_real,
        planned_workout_type=planned_workout_type,
        dominant_zone=dominant_zone,
        time_in_zones_pct=time_in_zones_pct,
        rpe=rpe,
        next_session_info=next_session_info,
        coaching_mode=coaching_mode,
    )

    try:
        provider = get_provider()
        raw = await provider.generate(
            system_prompt=coach_blocks_system_prompt(),
            user_message=user_message,
            max_tokens=3000,
        )
        # Nettoyer les éventuels backticks markdown
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:]).strip()
        if clean.endswith("```"):
            clean = clean.rsplit("```", 1)[0].strip()
        blocks = _json.loads(clean)
        for key in ("form_interpretation", "session_interpretation", "next_advice"):
            if key not in blocks or not isinstance(blocks[key], str):
                raise ValueError(f"Clé manquante ou invalide : {key}")
        return {
            "form_interpretation": blocks["form_interpretation"],
            "session_interpretation": blocks["session_interpretation"],
            "next_advice": blocks["next_advice"],
        }
    except Exception as exc:
        _log.warning("[generate_coach_blocks] Fallback déterministe. %s", exc)
        return _fallback()
