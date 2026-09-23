"""
Génère une analyse LLM d'une activité en comparant réalisé vs prévu.
Appelé depuis session_log.py après la capture du RPE.

Principe : les calculs de match score, détection RPE mismatch, et interprétation
des métriques sont déterministes — le LLM ne fait qu'interpréter les valeurs
pré-calculées, organisées en blocs thématiques.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    return (flag, f"TSS réalisé {actual:.0f} vs prévu {planned:.0f} ({sign}{pct_diff:.0f}%)")


def _detect_rpe_mismatch(
    rpe: float | None,
    actual_tss: float | None,
    planned_tss: float | None,
    actual_duration_minutes: int | None = None,
    planned_duration_minutes: int | None = None,
) -> str | None:
    """Détecte les incohérences entre RPE et TSS. Retourne un message d'alerte ou None."""
    if rpe is None:
        return "Pas de ressenti noté — pense à le renseigner post-séance 📝"

    if actual_tss is not None and planned_tss is not None and planned_tss > 0:
        ratio = actual_tss / planned_tss
        if rpe >= RPE_HARD_MIN and ratio < 0.85:
            return "Séance ressentie dure mais TSS inférieur au prévu — vérifie ton FTP ⚡"
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
            return "Séance ressentie facile malgré un TSS élevé — bonne forme ou FTP à revoir ?"

    return None


def _tsb_tone(tsb: float | None) -> str:
    """Directive tonalité alignée sur les seuils de tsb_label()."""
    if tsb is None:
        return "coaching équilibré"
    if tsb < -30:
        return "ton protecteur — récupération prioritaire, valide l'effort sans pousser"
    if tsb >= 5:
        return "ton motivant — souligne la progression, encourage la continuité"
    return "coaching équilibré et pédagogique"


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
    weekly_snapshot: "WeeklySnapshot | None" = None,
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

    lines = ["ANALYSE ACTIVITÉ :"]

    # ── Contexte Variable Reward (injecté en tête si fourni) ──────────────────
    if highlight_category:
        lines.append(
            f"\nMÉTRIQUE EN VEDETTE : {highlight_category}"
            "\n(L'athlète vient de voir cette métrique mise en avant dans le message précédent."
            " Construis ton analyse principalement autour d'elle.)"
        )
    if personal_record:
        lines.append(
            f"\nRECORD PERSONNEL DÉTECTÉ : {personal_record.get('label_fr', '')}"
            f" ({personal_record.get('value', '')} vs précédent {personal_record.get('previous_best', '')}"
            f" sur {personal_record.get('sessions_compared', '')} séances)"
            "\n(Mentionne ce record — c'est un moment mémorable pour l'athlète.)"
        )

    # ── Bloc 1 : Séance ──────────────────────────────────────────────────────
    lines.append("\n[SÉANCE]")
    if session_type_real:
        type_label = session_type_real.replace("_", " ").capitalize()
        if planned_workout_type:
            planned_label = planned_workout_type.replace("_", " ").capitalize()
            lines.append(f"- Type prévu : {planned_label} / Réalisé : {type_label}")
        else:
            lines.append(f"- Type séance détecté : {type_label}")
    elif planned_workout_type:
        lines.append(f"- Type prévu : {planned_workout_type.replace('_', ' ').capitalize()}")

    if duration_minutes:
        if planned_duration_minutes:
            lines.append(f"- Durée : {duration_minutes} min (prévu : {planned_duration_minutes} min)")
        else:
            lines.append(f"- Durée : {duration_minutes} min")
    if actual_tss is not None:
        lines.append(f"- TSS réalisé : {actual_tss:.0f}")

    if match_text:
        lines.append(f"- Comparaison plan : {match_flag} {match_text}")
    elif planned_tss is None:
        lines.append("- Comparaison plan : pas de séance prévue pour cette sortie")

    if rpe is not None:
        lines.append(f"- RPE : {rpe_label(rpe)}")
        if fatigue_anomaly:
            lines.append(
                f"- RPE cardiaque estimé : {fatigue_anomaly['rpe_cardiac_estimate']}/10"
                f" (écart : +{fatigue_anomaly['rpe_delta']})"
            )
    else:
        lines.append("- RPE : non renseigné")

    if rpe_hint:
        lines.append(f"- ⚠️ Alerte fatigue : {rpe_hint}")

    # ── Bloc 2 : Puissance ───────────────────────────────────────────────────
    power_lines: list[str] = []
    if normalized_power:
        power_lines.append(f"- NP : {normalized_power}W")

    if intensity_factor is not None:
        if intensity_factor >= 0.95:
            if_note = "séance quasi-maximale"
        elif intensity_factor >= 0.85:
            if_note = "intensité seuil/tempo"
        elif intensity_factor >= 0.75:
            if_note = "zone sweet spot"
        else:
            if_note = "endurance / récupération"
        power_lines.append(f"- IF : {intensity_factor:.2f} ({if_note})")

    # VI ignoré si durée < 30 min (pas représentatif sur courtes sorties)
    if variability_index is not None and (duration_minutes or 0) >= 30:
        vi_note = "effort régulier" if variability_index < 1.05 else "effort variable/nerveux"
        power_lines.append(f"- VI : {variability_index:.2f} ({vi_note})")

    if power_lines:
        lines.append("\n[PUISSANCE]")
        lines.extend(power_lines)

    # ── Bloc 3 : Qualité ─────────────────────────────────────────────────────
    quality_lines: list[str] = []
    if dominant_zone:
        quality_lines.append(f"- Zone dominante : {dominant_zone}")

    if time_in_zones_s:
        zone_detail = " / ".join(
            f"{z}={int(s/60)}min"
            for z, s in sorted(time_in_zones_s.items())
            if s > 0
        )
        quality_lines.append(f"- Distribution zones : {zone_detail}")

    if respect_zones_score is not None:
        rz_label = "✅ respecté" if respect_zones_score >= 80 else "⚠️ à améliorer"
        quality_lines.append(f"- Respect des zones : {respect_zones_score:.0f}/100 {rz_label}")

    if cardiac_drift_index is not None:
        drift_pct = cardiac_drift_index * 100
        drift_note = "OK" if abs(drift_pct) < 10 else "dérive notable → surveiller hydratation/chaleur"
        quality_lines.append(f"- Drift cardiaque : {drift_pct:+.1f}% ({drift_note})")

    if intervals_consistency_index is not None:
        ci_pct = round(intervals_consistency_index * 100)
        ci_note = "réguliers" if ci_pct >= 80 else "irréguliers"
        quality_lines.append(f"- Consistance intervalles : {ci_pct}% ({ci_note})")

    if quality_lines:
        lines.append("\n[QUALITÉ]")
        lines.extend(quality_lines)

    # ── Bloc 4 : Contexte extérieur ──────────────────────────────────────────
    context_lines: list[str] = []
    if elevation_gain_m:
        context_lines.append(f"- Dénivelé : {elevation_gain_m:.0f}m")
    if average_temp_c is not None:
        context_lines.append(f"- Température : {average_temp_c:.0f}°C")
    if is_group_ride:
        context_lines.append("- Sortie en groupe (aspiration possible)")

    if context_lines:
        lines.append("\n[CONTEXTE]")
        lines.extend(context_lines)

    # ── Bloc 5 : Forme & charge hebdomadaire ─────────────────────────────────
    pmc_lines: list[str] = []
    if ctl is not None and atl is not None and tsb is not None:
        pmc_lines.append(f"- CTL {ctl:.0f} · ATL {atl:.0f} · TSB {tsb:+.0f}")
        pmc_lines.append(f"- Directive tonalité : {_tsb_tone(tsb)}")

    if weekly_snapshot is not None:
        snap = weekly_snapshot
        if snap.tss_6w_avg > 0:
            pmc_lines.append(f"- Charge 7j : {snap.tss_7d:.0f} TSS (tendance : {snap.load_trend_pct:+.0f}% vs moy 6 sem {snap.tss_6w_avg:.0f})")
        else:
            pmc_lines.append(f"- Charge 7j : {snap.tss_7d:.0f} TSS (historique < 6 sem)")
        done_count = sessions_done_week if sessions_done_week is not None else snap.sessions_done_7d
        if sessions_planned_week is not None:
            remaining = max(0, sessions_planned_week - done_count)
            pmc_lines.append(f"- Séances cette semaine : {done_count}/{sessions_planned_week} réalisées, {remaining} restante(s)")
        else:
            pmc_lines.append(f"- Séances réalisées cette semaine : {done_count}")
        if next_session_info:
            pmc_lines.append(f"- Prochaine séance : {next_session_info}")
        if snap.monotony_index is not None:
            from app.engine.guardrail_thresholds import MONOTONY_HIGH

            mono_note = (
                "charge monotone → varier les intensités"
                if snap.monotony_index > MONOTONY_HIGH
                else "bonne variété des charges"
            )
            pmc_lines.append(
                f"- Monotonie (Foster) : {snap.monotony_index:.1f} "
                f"({mono_note} ; >{MONOTONY_HIGH:.1f} = danger)"
            )

    if pmc_lines:
        lines.append("\n[FORME & CHARGE]")
        lines.extend(pmc_lines)

    # ── Instruction de structure ──────────────────────────────────────────────
    # En mode narratif, la structure est définie dans le system prompt (build_narrative_system_prompt).
    # En mode classique, on conserve la structure explicite ci-dessous.
    if not storytelling_mode:
        lines.append(
            "\nStructure de ta réponse :"
            "\n1. 1 phrase résumé de la séance (type + intensité perçue)."
            "\n2. 1 phrase sur le point technique le plus pertinent"
            " (IF/VI pour une séance power, drift ou consistance pour intervalles, zones pour endurance)."
            "\n3. 1 phrase lecture de forme : TSB + tendance de charge si disponibles."
            "\n4. 1 reco concrète et actionnable (optionnel, uniquement si pertinente)."
        )

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
            parts.append(f"🏆 {personal_record.get('label_fr', '')}.")
        if match_text:
            parts.append(f"{match_flag} {match_text}.")
        if rpe_hint:
            parts.append(rpe_hint)
        elif rpe is not None:
            parts.append(f"Ressenti : {rpe_label(rpe)}.")
        if tsb is not None:
            parts.append(f"TSB actuel : {tsb:+.0f}.")
        return " ".join(parts) if parts else "Séance enregistrée ✅"


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
    from app.llm.factory import get_provider
    from app.llm.prompts import COACH_BLOCKS_SYSTEM_PROMPT, build_coach_blocks_user_message

    _log = _logging.getLogger(__name__)

    def _fallback() -> dict[str, str]:
        if coaching_mode == "freestyle":
            return {
                "form_interpretation": tsb_label_str or "Données de forme calculées.",
                "session_interpretation": "Sortie libre enregistrée avec ton ressenti.",
                "next_advice": (
                    "Si tu veux, je peux te proposer une prochaine sortie selon ton envie "
                    "et ton temps disponible."
                ),
            }
        return {
            "form_interpretation": tsb_label_str or "Données de forme calculées.",
            "session_interpretation": "Séance enregistrée et comptabilisée dans ton plan.",
            "next_advice": next_session_info or "Consulte ton plan pour la prochaine séance.",
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
            system_prompt=COACH_BLOCKS_SYSTEM_PROMPT,
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
