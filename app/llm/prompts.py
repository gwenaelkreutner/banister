from app.core.localization import t


def _coach_soul(first_name: str) -> str:
    return t("llm.coach_soul", first_name=first_name)


def plan_intro_system_prompt() -> str:
    return t("llm.plan_intro.system_prompt")


def plan_intro_user_message(**values: object) -> str:
    return t("llm.plan_intro.user_template", **values)


def week_system_prompt() -> str:
    return t("llm.week.system_prompt")


def week_user_message(**values: object) -> str:
    return t("llm.week.user_template", **values)

# ── UXWriting — System prompt adaptatif par niveau de vocabulaire ─────────────

_NARRATIVE_MODES = ("journalist", "analyst", "coach")


def build_narrative_system_prompt(user_level: int, mode: str) -> str:
    """System prompt pour l'analyse post-séance en mode narratif (3 personas).

    Args:
        user_level: 0=Débutant, 1=Amateur, 2=Intermédiaire
        mode: "journalist" | "analyst" | "coach"

    Returns:
        System prompt string à passer au LLM.
    """
    lvl = max(0, min(2, user_level))
    mode = mode if mode in _NARRATIVE_MODES else "coach"
    persona = t(f"llm.narrative_mode.{mode}.persona")
    style = t(f"llm.narrative_mode.{mode}.style")
    ctl_term = t(f"llm.vocab.ctl.{lvl}")
    atl_term = t(f"llm.vocab.atl.{lvl}")
    tsb_term = t(f"llm.vocab.tsb.{lvl}")
    tss_term = t(f"llm.vocab.tss.{lvl}")
    level_ctx = t(f"llm.level_context.{lvl}")
    vocab_line = t("llm.vocab_line", ctl=ctl_term, atl=atl_term, tsb=tsb_term, tss=tss_term)

    acronym_ban = t("llm.acronym_ban_with_tu") if lvl < 2 else ""

    return t(
        "llm.narrative.system_prompt",
        persona=persona,
        level_ctx=level_ctx,
        vocab_line=vocab_line,
        acronym_ban=acronym_ban,
        style=style,
    )


# ── Récap hebdomadaire ────────────────────────────────────────────────────────

def weekly_recap_system_prompt() -> str:
    return t("llm.weekly_recap.system_prompt")


def weekly_recap_coach_message(**values: object) -> str:
    return t("llm.weekly_recap.coach_template", **values)


def weekly_recap_nextweek_message(**values: object) -> str:
    return t("llm.weekly_recap.nextweek_template", **values)


_RECAP_TONE_KEYS = {
    "critical": "llm.recap_tone.critical",
    "celebratory": "llm.recap_tone.celebratory",
    "enthusiastic": "llm.recap_tone.enthusiastic",
    "understanding": "llm.recap_tone.understanding",
    "balanced": "llm.recap_tone.balanced",
}


def recap_tone_directive(kind: str) -> str:
    return t(_RECAP_TONE_KEYS[kind])


# spec 006 T020 / FR-003 / SC-008 — appended to the system prompt only when a workload
# guardrail with an above-range acute:chronic ratio (or a high ramp rate) is present.
# The finding's own action already says "reduce load"; this is the meta-rule that the
# rest of the response must not contradict it.
def guardrail_load_reduction_rule() -> str:
    return t("llm.guardrail_load_reduction_rule")

# spec 010 — appended to the system prompt only in freestyle mode (no active plan).
# Found live (2026-09-18) : sans cette règle, seule la toute première demande de séance
# d'une conversation neuve déclenche fiablement l'outil ; dès qu'une négociation suit
# ("plus dur", "moins dur", "publie-la"), le modèle décrit une séance de sa propre
# initiative — l'historique ne rejoue jamais les appels d'outils passés (seulement le
# texte final), donc rien dans le contexte visible ne rappelle au modèle qu'un outil
# existe pour ça, tour après tour. Cette règle est réinjectée en entier à chaque tour
# (contrairement à l'historique qui s'érode), donc elle ne dépend pas de ce que le
# modèle a "vu" plus tôt dans la conversation.
def freestyle_session_tool_rule() -> str:
    return t("llm.freestyle_session_tool_rule")

# spec 006 FR-028 / SC-009 — shown before the first coaching interaction (end of /setup)
# and in the README. A single constant so spec 007's first-run flow relocates it rather
# than rewriting it (research R7).
def disclaimer_text() -> str:
    return t("llm.disclaimer_text")


# spec 006 FR-029 / FR-030 — scope-of-advice rules, appended to every system prompt.
def scope_of_advice_rules() -> str:
    return t("llm.scope_of_advice_rules")


def build_ux_system_prompt(user_level: int, persona=None, first_name: str | None = None) -> str:
    """Retourne le system prompt UXWriting avec vocabulaire adapté au niveau.

    Args:
        user_level: 0=Débutant, 1=Amateur, 2=Intermédiaire
        persona: si fourni (spec 007 US5), sa voix `ux_prompt` remplace le texte
          "Coach" par défaut. Le vocabulaire adapté au niveau et les règles spec 006
          (scope-of-advice) restent ajoutés dans tous les cas.
        first_name: si fourni, ajoute le bloc d'identité du coach (persona ou
          COACH_SOUL) en fin de prompt — c'est ce bloc, 100 % statique, qui vivait
          auparavant à la fin de app/llm/tools.py::build_system_prompt() (préfixe
          stable / queue volatile, voir doc de revue Enduragent 2026-09-20). Laisser
          à None préserve le comportement historique pour les appelants qui n'ont
          jamais eu ce bloc (app/bot/routers/forme.py, app/llm/activity_analysis.py).

    Returns:
        System prompt string à passer au LLM.
    """
    lvl = max(0, min(2, user_level))
    ctl_term = t(f"llm.vocab.ctl.{lvl}")
    atl_term = t(f"llm.vocab.atl.{lvl}")
    tsb_term = t(f"llm.vocab.tsb.{lvl}")
    tss_term = t(f"llm.vocab.tss.{lvl}")
    level_ctx = t(f"llm.level_context.{lvl}")
    vocab_line = t("llm.vocab_line", ctl=ctl_term, atl=atl_term, tsb=tsb_term, tss=tss_term)

    acronym_ban = t("llm.acronym_ban") if lvl < 2 else ""
    level_and_rules = (
        f"{level_ctx} "
        f"{vocab_line}"
        f"{acronym_ban}"
        "Interprète les données, ne recalcule jamais. "
        f"{scope_of_advice_rules()}"
    )

    identity_block = ""
    if first_name is not None:
        identity = (
            persona.format_system_prompt(first_name=first_name)
            if persona is not None
            else _coach_soul(first_name)
        )
        identity_block = f"\n\n{identity}"

    if persona is not None:
        return f"{persona.ux_prompt.strip()}\n\n{level_and_rules}{identity_block}"

    return t(
        "llm.ux_default.system_prompt",
        level_ctx=level_ctx,
        vocab_line=vocab_line,
        acronym_ban=acronym_ban,
        scope_rules=scope_of_advice_rules(),
        identity_block=identity_block,
    )


# ── Coach blocks — analyse post-séance structurée ────────────────────────────

def coach_blocks_system_prompt() -> str:
    return t("llm.coach_blocks.system_prompt")


def build_coach_blocks_user_message(
    *,
    tsb: float | None,
    tsb_label_str: str | None,
    load_trend_pct: float | None,
    tss_6w_daily_avg: float | None,
    sessions_done_week: int | None,
    sessions_planned_week: int | None,
    tss_done_week: float | None = None,
    week_tss_target: float | None = None,
    tss_actual: float | None,
    tss_planned: float | None,
    session_type_real: str | None,
    planned_workout_type: str | None,
    dominant_zone: str | None,
    time_in_zones_pct: dict | None,
    rpe: float | None,
    next_session_info: str | None,
    coaching_mode: str = "goal",
) -> str:
    """Construit le message utilisateur pour generate_coach_blocks."""
    lines = [t("llm.coach_blocks_data.header")]
    if coaching_mode == "freestyle":
        lines.append(t("llm.coach_blocks_data.freestyle_frame"))

    # Contexte semaine en cours (prioritaire pour form_interpretation)
    if sessions_done_week is not None and sessions_planned_week is not None:
        remaining = max(0, sessions_planned_week - sessions_done_week)
        tss_week_str = ""
        if tss_done_week is not None and week_tss_target:
            tss_week_str = t(
                "llm.coach_blocks_data.tss_week_suffix",
                done=f"{tss_done_week:.0f}", target=f"{week_tss_target:.0f}",
            )
        lines.append(t(
            "llm.coach_blocks_data.current_week",
            done=sessions_done_week, planned=sessions_planned_week,
            tss_week=tss_week_str, remaining=remaining,
        ))
    if tsb is not None:
        lines.append(t(
            "llm.coach_blocks_data.form_balance", tsb=f"{tsb:+.0f}", label=tsb_label_str or ""
        ))
    if load_trend_pct is not None:
        trend_dir = t("llm.review_data.trend_up") if load_trend_pct > 0 else t("llm.review_data.trend_down")
        lines.append(t(
            "llm.coach_blocks_data.load_trend", pct=f"{load_trend_pct:+.0f}", direction=trend_dir
        ))

    lines.append("")
    if session_type_real:
        lines.append(t("llm.coach_blocks_data.type_real", type=session_type_real))
    if planned_workout_type:
        lines.append(t("llm.coach_blocks_data.type_planned", type=planned_workout_type))
    if tss_actual is not None:
        tss_line = t("llm.coach_blocks_data.session_load", tss=f"{tss_actual:.0f}")
        if tss_planned:
            pct = (tss_actual / tss_planned - 1) * 100
            sign = "+" if pct >= 0 else ""
            tss_line += t(
                "llm.coach_blocks_data.session_load_planned_suffix",
                planned=f"{tss_planned:.0f}", sign=sign, pct=f"{pct:.0f}",
            )
        lines.append(tss_line)
    if rpe is not None:
        from app.engine.rpe import rpe_label

        lines.append(t("llm.coach_blocks_data.rpe", label=rpe_label(rpe)))
    if dominant_zone:
        lines.append(t("llm.coach_blocks_data.dominant_zone", zone=dominant_zone))
    if time_in_zones_pct:
        pct_str = " / ".join(
            f"{z}={v}%" for z, v in sorted(time_in_zones_pct.items()) if v > 0
        )
        lines.append(t("llm.coach_blocks_data.zone_distribution", distribution=pct_str))

    if next_session_info:
        lines.append(t("llm.coach_blocks_data.next_session", info=next_session_info))

    return "\n".join(lines)


_TID_CLASSIFICATION_KEYS = {
    "polarized": "llm.tid.polarized",
    "pyramidal": "llm.tid.pyramidal",
    "threshold": "llm.tid.threshold",
    "high_intensity": "llm.tid.high_intensity",
    "base": "llm.tid.base",
    "unclassified": "llm.tid.unclassified",
}


def tid_classification_label(classification: str) -> str:
    key = _TID_CLASSIFICATION_KEYS.get(classification)
    return t(key) if key else classification

# ── /review — synthèse de séance à la demande ────────────────────────────────

# Un seul format (150-200 mots) — un ancien plan à 3 profondeurs (brief/default/deep)
# ne se différenciait que par ces deux consignes molles, jamais appliquées par force
# (même max_tokens, même structure) : en pratique les 3 sorties convergeaient. Un seul
# mode bien calibré vaut mieux (décision owner, 2026-09-21). Localisé : voir
# llm.review.word_budget / llm.review.vocab_rule dans les catalogues.

# Blocs de données optionnels du message utilisateur — chacun indépendamment togglable
# (False = retiré du prompt, zéro autre changement) si un bloc s'avère bruyant ou
# trompeur en usage réel, sans repasser par le code qui construit les lignes. Ajouté
# 2026-09-21 après une relecture des logs /review : les champs sources ci-dessous
# existaient déjà sur SessionLog (calculés à l'ingestion, mapper.py) mais n'étaient
# jamais montés dans ce prompt — pas une exclusion documentée, juste jamais branché.
REVIEW_DATA_BLOCKS = {
    # normalized_power, intensity_factor — mêmes libellés/notes que activity_analysis.py
    # (bloc [PUISSANCE]), pour rester cohérent avec le reste de l'app.
    "raw_power": True,
    # cardiac_drift_index, intervals_consistency_index, respect_zones_score,
    # variability_index — idem, mêmes libellés que activity_analysis.py (bloc [QUALITÉ]).
    "quality_signals": True,
    # elevation_gain_m, average_temp_c, kilojoules — rebranchés le 2026-09-21 :
    # AnalyzedSession/mapper.py/activity_feedback.py ne portaient pas ces trois champs
    # jusqu'ici (vérifié contre un payload réel intervals.icu avant d'écrire le mapping —
    # total_elevation_gain/average_temp/icu_joules existent bien, déjà dans les bonnes
    # unités). None sur une sortie indoor (VirtualRide, pas de capteur météo) — jamais 0.
    "environmental": True,
    # recovery_index, detected_phase — recalculés à log.logged_date (pas "aujourd'hui"
    # comme le chat), voir assemble_review_context(). `None` sur ce compte de test tant
    # qu'il n'a pas de VFC/FC repos récentes (recovery_index) ou d'historique (phase).
    "form_context": True,
    # hydration_volume_l, kcal_consumed — wellness du jour de la séance tel que renseigné
    # sur intervals.icu (pas notre suivi calorique chat, table séparée — /log_meal). Champs
    # bruts déjà stockés côté wellness mais jamais montrés avant ce câblage (2026-09-21).
    "nutrition_context": True,
}

# athlete_count reste hors scope, lui, pour une raison différente des trois champs
# ci-dessus : vérifié contre le payload réel (2026-09-21) — il n'existe tout simplement
# aucun champ de comptage d'athlètes sur une activité intervals.icu. Le seul champ
# apparenté est `group` (l'id de corrélation d'une sortie de groupe, une string opaque
# partagée entre les activités des participants) — reconstruire un compte demanderait de
# recouper les activités d'autres comptes que le nôtre, hors de portée d'une clé API
# personnelle. Confirmé mort après spec 002 (dépendait de Strava, l'utilisateur l'a
# signalé en testant en conditions réelles) et rien à rebrancher côté source.

def build_review_system_prompt(has_rpe: bool, coaching_mode: str = "goal") -> str:
    """Prompt système pour la synthèse `/review` — un seul appel one-shot par revue
    (comme template_picker.py/narrator.py), jamais la boucle agentique : toutes les
    données sont déjà assemblées par assemble_review_context() avant l'appel."""
    rpe_block = "" if has_rpe else f"\n{t('llm.review.rpe_missing_rule')}"
    freestyle_rule = t("llm.review.freestyle_frame") if coaching_mode == "freestyle" else ""

    return t(
        "llm.review.system_prompt",
        vocab_rule=t("llm.review.vocab_rule"),
        word_budget=t("llm.review.word_budget"),
        freestyle_rule=freestyle_rule,
        rpe_block=rpe_block,
    )


def build_review_user_message(ctx, dfa=None) -> str:
    """Construit le message utilisateur pour generate_session_review(). `ctx` est un
    ReviewContext (app/services/session_review.py) — import non typé ici pour éviter un
    cycle prompts.py ↔ services/. `dfa` : DFABlock (app/engine/dfa.py) calculé par
    l'appelant si l'activité a un enregistrement AlphaHRV — None sinon (aucun
    enregistrement, ou pas d'`source_activity_id` pour logguer manuel)."""
    log = ctx.log
    lines = [t("llm.review_data.header")]
    if ctx.coaching_mode == "freestyle":
        lines.append(t("llm.review_data.freestyle_frame"))
    else:
        lines.append(t("llm.review_data.plan_frame"))
    lines.append(t("llm.review_data.date", date=f"{log.logged_date:%d/%m/%Y}"))
    if log.session_type_real:
        lines.append(t("llm.review_data.type_real", type=log.session_type_real))
    if log.duration_minutes_actual is not None:
        lines.append(t("llm.review_data.duration", minutes=log.duration_minutes_actual))
    if log.tss_actual is not None:
        tss_line = t("llm.review_data.tss", tss=f"{log.tss_actual:.0f}")
        if ctx.session_spec is not None:
            tss_line += t(
                "llm.review_data.tss_planned_suffix", planned=f"{ctx.session_spec.tss_target:.0f}"
            )
        lines.append(tss_line)
    if ctx.session_spec is not None:
        lines.append(t("llm.review_data.type_planned", type=ctx.session_spec.workout_type))
    if log.dominant_zone:
        lines.append(t("llm.review_data.dominant_zone", zone=log.dominant_zone))
    if log.avg_power is not None:
        lines.append(t("llm.review_data.avg_power", watts=log.avg_power))
    if log.avg_heart_rate is not None:
        lines.append(t("llm.review_data.avg_hr", bpm=log.avg_heart_rate))

    if REVIEW_DATA_BLOCKS["raw_power"]:
        if log.normalized_power:
            lines.append(t("llm.review_data.normalized_power", watts=log.normalized_power))
        if log.intensity_factor is not None:
            lines.append(t("llm.review_data.intensity_factor", value=f"{log.intensity_factor:.2f}"))
        # VI ignoré si durée < 30 min — pas représentatif sur courtes sorties (même règle
        # que activity_analysis.py).
        if log.variability_index is not None and (log.duration_minutes_actual or 0) >= 30:
            lines.append(
                t("llm.review_data.variability_index", value=f"{log.variability_index:.2f}")
            )

    if log.efficiency_factor is not None:
        lines.append(t("llm.review_data.efficiency_factor", value=f"{log.efficiency_factor:.2f}"))
    if log.hrr is not None:
        lines.append(t("llm.review_data.hrr", value=f"{log.hrr:.0f}"))

    if REVIEW_DATA_BLOCKS["quality_signals"]:
        if log.respect_zones_score is not None:
            lines.append(t("llm.review_data.zone_adherence", score=f"{log.respect_zones_score:.0f}"))
        if log.cardiac_drift_index is not None:
            lines.append(
                t("llm.review_data.cardiac_drift", pct=f"{log.cardiac_drift_index * 100:+.1f}")
            )
        if log.intervals_consistency_index is not None:
            lines.append(t(
                "llm.review_data.intervals_consistency",
                pct=round(log.intervals_consistency_index * 100),
            ))

    if REVIEW_DATA_BLOCKS["environmental"]:
        if log.elevation_gain_m:
            lines.append(t("llm.review_data.elevation", meters=f"{log.elevation_gain_m:.0f}"))
        if log.average_temp_c is not None:
            lines.append(t("llm.review_data.avg_temp", temp=f"{log.average_temp_c:.0f}"))
        if log.kilojoules:
            lines.append(t("llm.review_data.energy", kj=f"{log.kilojoules:.0f}"))

    if dfa is not None and dfa.quality.sufficient:
        dfa_line = t("llm.review_data.dfa_avg", value=f"{dfa.avg:.2f}")
        if dfa.lt1_crossing is not None and dfa.lt1_crossing.avg_hr is not None:
            dfa_line += t("llm.review_data.dfa_lt1", bpm=dfa.lt1_crossing.avg_hr)
            if dfa.lt2_crossing is not None and dfa.lt2_crossing.avg_hr is not None:
                dfa_line += t("llm.review_data.dfa_lt2", bpm=dfa.lt2_crossing.avg_hr)
            dfa_line += ")"
        lines.append(dfa_line)

    if log.rpe is not None:
        from app.engine.rpe import rpe_label

        lines.append(t("llm.review_data.rpe", label=rpe_label(log.rpe)))
    else:
        lines.append(t("llm.review_data.rpe_missing"))

    if ctx.fitness_at_session is not None:
        f = ctx.fitness_at_session
        lines.append(t(
            "llm.review_data.fitness_at_session",
            tsb=f"{f.tsb:+.0f}", ctl=f"{f.ctl:.0f}", atl=f"{f.atl:.0f}",
        ))

    if REVIEW_DATA_BLOCKS["form_context"]:
        if ctx.recovery_index is not None:
            lines.append(t("llm.review_data.recovery_index", value=f"{ctx.recovery_index:.2f}"))
        if ctx.detected_phase is not None:
            # Même vocabulaire que le chat (app/llm/tools.py) — voir narrator.phase_label().
            from app.llm.narrator import phase_label as _phase_label

            phase_lbl = _phase_label(ctx.detected_phase.detected_phase)
            agree_note = ""
            if ctx.detected_phase.streams_agree is False:
                secondary_label = _phase_label(ctx.detected_phase.secondary_phase)
                agree_note = t("llm.review_data.phase_agree_note", phase=secondary_label)
            lines.append(t(
                "llm.review_data.detected_phase", phase=phase_lbl, agree_note=agree_note
            ))

    if REVIEW_DATA_BLOCKS["nutrition_context"]:
        if ctx.kcal_consumed is not None:
            lines.append(t("llm.review_data.kcal", kcal=ctx.kcal_consumed))
        if ctx.hydration_volume_l is not None:
            lines.append(
                t("llm.review_data.hydration", liters=f"{ctx.hydration_volume_l:.1f}")
            )

    snap = ctx.weekly_snapshot
    if snap.monotony_index is not None:
        lines.append(t("llm.review_data.monotony", value=snap.monotony_index))
    if snap.load_trend_pct is not None:
        trend_dir = (
            t("llm.review_data.trend_up") if snap.load_trend_pct > 0
            else t("llm.review_data.trend_down")
        )
        lines.append(t(
            "llm.review_data.load_trend",
            pct=f"{snap.load_trend_pct:+.0f}", direction=trend_dir,
        ))

    if ctx.tid is not None:
        tid_label = tid_classification_label(ctx.tid.classification)
        lines.append(t(
            "llm.review_data.tid",
            label=tid_label,
            z1=f"{ctx.tid.zone1_pct:.0f}", z2=f"{ctx.tid.zone2_pct:.0f}", z3=f"{ctx.tid.zone3_pct:.0f}",
        ))

    return "\n".join(lines)
