"""
Orchestrateur du chat agentique.

Flux :
  1. Charge le contexte (profil, métriques, historique)
  2. Construit le system prompt
  3. Appelle run_agentic_loop avec les 4 outils
  4. Le tool_executor exécute le code déterministe et retourne les résultats
  5. Retourne (response_text, intent, tool_used)
"""

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics, compute_fitness_from_any, tsb_label
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.llm.chat_client import run_agentic_loop
from app.llm.tools import build_context_messages, build_system_prompt
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)

DAY_NAMES_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
WORKOUT_FR = {
    "long_ride": "Sortie longue",
    "intervals": "Intervalles",
    "endurance": "Endurance",
    "recovery": "Récupération",
}


async def run_chat(
    user_message: str,
    user: User,
    session: AsyncSession,
    reply_context: str | None = None,
) -> tuple[str, str | None, str | None, dict | None, dict, str | None]:
    """
    Exécute le cycle de chat agentique pour un message utilisateur.

    Retourne (response_text, intent, tool_used_name, pending_proposal, usage, meal_log).
    intent est déterminé a posteriori selon l'outil appelé. `usage` (tokens
    prompt/completion/total, nombre d'appels API) sert à mesurer le coût réel d'un tour
    de chat — voir app/services/token_usage.py et scripts/token_usage_state.py. `meal_log`
    est une confirmation déterministe (pas du texte LLM) de ce que `log_meal`/
    `undo_last_meal_entry` ont réellement écrit en base ce tour, destinée à être envoyée
    comme second message Telegram séparé façon log — `None` si aucun des deux n'a tourné.

    `reply_context` : texte du message Telegram auquel l'athlète a répondu en reply-quote
    (n'importe quel message du bot — chat, /review, /recap, notification — puisque
    Telegram renvoie ce texte brut dans le payload, indépendamment de ce que Banister a
    lui-même loggé dans `chat_messages`). `None` si le message n'est pas une réponse.
    """
    # 1. Charger le contexte
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    history = await repo.chat_repo.get_conversation(session, user.id, limit=8)

    # Activités importées pré-plan (sans double-comptage avec session_logs)
    activities = await repo.activity_repo.get_for_user(session, user.id, days=365)
    plan_start = plan.start_date if plan else date.today()
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]
    all_items = pre_plan_acts + logs

    profile: AthleteProfileSchema | None = None
    if profile_row and profile_row.profile:
        try:
            profile = AthleteProfileSchema.model_validate(profile_row.profile)
        except Exception:
            logger.warning("Impossible de valider le profil athlète")

    current = await get_current_fitness(session, user.id)
    metrics: FitnessMetrics | None = (
        current.metrics if current is not None
        else (compute_fitness_from_any(all_items) if all_items else None)
    )

    # Convertir le plan SQLAlchemy en schema Pydantic pour build_system_prompt
    plan_schema: TrainingPlanSchema | None = None
    if plan and plan.plan_technical:
        try:
            plan_schema = TrainingPlanSchema.model_validate(plan.plan_technical)
            plan_schema.start_date = plan.start_date
        except Exception:
            logger.warning("Impossible de valider le plan technique")

    # 2. Construire system prompt et messages
    user_level: int = getattr(profile, "user_level", 0) if profile else 0
    from app.llm.prompts import build_ux_system_prompt
    from app.services.coach_voice import resolve_voice

    _persona, _voice_fell_back = resolve_voice(user)
    ux_rules = build_ux_system_prompt(
        user_level, persona=_persona, first_name=user.first_name or "l'athlète"
    )
    def _item_date(item):
        return item.logged_date if hasattr(item, "logged_date") else item.activity_date

    recent_items = sorted(list(pre_plan_acts) + list(logs or []), key=_item_date)[-7:]
    # Calendrier intervals.icu publié : si le plan a évolué depuis, le dire au coach
    # plutôt que de laisser croire que le calendrier est à jour (spec 005 FR-020).
    calendar_divergence: str | None = None
    if plan is not None and plan_schema is not None:
        try:
            from app.db.repositories import publication_repo
            from app.services.publication import describe_divergence_for_coach

            live_entries = await publication_repo.get_active_entries_for_plan(
                session, user.id, plan.id
            )
            if live_entries:
                calendar_divergence = describe_divergence_for_coach(plan_schema, live_entries)
        except Exception:
            logger.warning("Impossible de calculer la divergence calendrier")

    # Garde-fous d'entraînement (spec 006) : signaux calculés par le moteur déterministe.
    from app.services.guardrail_service import (
        assemble_recovery_findings,
        assemble_workload_findings,
        has_load_reduction_finding,
    )

    guardrail_findings: list = []
    recovery_gap: str | None = None
    try:
        _today = date.today()
        # Séance dure prévue aujourd'hui ? (pour l'énoncé de conflit FR-012)
        prescribed_type: str | None = None
        prescribed_zone: str | None = None
        if plan_schema and plan_schema.start_date:
            _wk = (_today - plan_schema.start_date).days // 7 + 1
            _week = next((w for w in plan_schema.weeks if w.week_number == _wk), None)
            if _week:
                _sess = next(
                    (s for s in _week.sessions if s.day_of_week == _today.weekday()), None
                )
                if _sess:
                    prescribed_type, prescribed_zone = _sess.workout_type, _sess.zone_code

        workload = await assemble_workload_findings(session, user.id, today=_today)
        recovery = await assemble_recovery_findings(
            session,
            user.id,
            prescribed_workout_type=prescribed_type,
            prescribed_zone=prescribed_zone,
            today=_today,
        )
        guardrail_findings = sorted(
            workload + recovery, key=lambda f: f.severity, reverse=True
        )
        if not recovery:
            from app.services.guardrail_service import recovery_insufficiency

            recovery_gap = await recovery_insufficiency(session, user.id, today=_today)
    except Exception:
        logger.warning("Impossible de calculer les signaux garde-fous")

    # Wellness du jour (sommeil/fatigue/stress/mood/motivation) — Section11-inspired
    # (2026-09-21), même journée que le registre de métriques ci-dessous.
    wellness_today = None
    try:
        wellness_today = await repo.wellness_repo.get_by_date(session, user.id, date.today())
    except Exception:
        logger.warning("Impossible de charger le wellness du jour")

    # Phase diagnostique (2026-09-21, porté de Section11) — distincte de week.phase
    # (prescriptif, choisi à la construction du plan) : inférée du comportement récent,
    # recoupée avec la phase déclarée par le plan actif ou la proximité de la date cible.
    detected_phase_result = None
    try:
        from app.engine.phase_detection import detect_training_phase

        _plan_week_phase: str | None = None
        if plan_schema and plan_schema.start_date:
            _wk_num = (date.today() - plan_schema.start_date).days // 7 + 1
            _cur_week = next((w for w in plan_schema.weeks if w.week_number == _wk_num), None)
            if _cur_week:
                _plan_week_phase = _cur_week.phase
        _target_date = profile.objective.target_date if profile else None
        detected_phase_result = detect_training_phase(
            all_items,
            today=date.today(),
            plan_week_phase=_plan_week_phase,
            target_date=_target_date,
        )
    except Exception:
        logger.warning("Impossible de calculer la phase diagnostique")

    # Calculé une seule fois : sert à la fois l'affichage FORME ACTUELLE (recovery_index)
    # et le MetricRegistry plus bas (mêmes valeurs, pas une seconde requête).
    registry_metrics: dict[str, float] = {}
    try:
        from app.services.guardrail_service import collect_registry_metrics

        registry_metrics = await collect_registry_metrics(session, user.id)
    except Exception:
        logger.warning("Impossible de collecter les métriques garde-fous pour le registre")

    coaching_ctx = build_system_prompt(
        first_name=user.first_name or "l'athlète",
        profile=profile,
        metrics=metrics,
        recent_logs=recent_items,
        plan=plan_schema,
        today=date.today(),
        session_logs=logs,  # permet d'afficher plan + réalisé pour la semaine en cours
        coach_memory=list(profile_row.coach_memory or []) if profile_row else None,
        athlete_notes=dict(profile_row.athlete_notes or {}) if profile_row else None,
        calendar_divergence=calendar_divergence,
        guardrail_findings=guardrail_findings,
        recovery_insufficiency=recovery_gap,
        wellness_today=wellness_today,
        recovery_index=registry_metrics.get("recovery_index"),
        detected_phase=detected_phase_result,
    )
    system = f"{ux_rules}\n\n---\n\n{coaching_ctx}"
    if has_load_reduction_finding(guardrail_findings):
        from app.llm.prompts import GUARDRAIL_LOAD_REDUCTION_RULE

        system = f"{system}\n\n{GUARDRAIL_LOAD_REDUCTION_RULE}"

    # spec 010 : réinjectée en entier à chaque tour (contrairement à l'historique, qui ne
    # rejoue jamais les appels d'outils passés — trouvé en live, voir prompts.py).
    if plan is None:
        from app.llm.prompts import FREESTYLE_SESSION_TOOL_RULE

        system = f"{system}\n\n{FREESTYLE_SESSION_TOOL_RULE}"

    # Registre des métriques mises devant le modèle — définit ce qui est « retrouvé »
    # pour la vérification de réponse (spec 006 US3, FR-018).
    from app.services.response_verification import (
        MetricRegistry,
        apply_result,
        verify_response,
    )

    registry = MetricRegistry()
    if metrics is not None:
        registry.register("ctl", metrics.ctl)
        registry.register("atl", metrics.atl)
        registry.register("tsb", metrics.tsb)
    if profile is not None and profile.equipment.ftp:
        registry.register("ftp", profile.equipment.ftp)
    for _name, _value in registry_metrics.items():
        registry.register(_name, _value)

    messages = build_context_messages(history)
    turn_content = user_message
    if reply_context:
        turn_content = (
            f'[L\'athlète répond directement à ce message précédent : "{reply_context}"]\n'
            f"{user_message}"
        )
    messages.append({"role": "user", "content": turn_content})

    # 3. Définir le tool_executor (fermeture sur session/plan/profile)
    async def tool_executor(name: str, args: dict) -> dict:
        return await _execute_tool(
            name, args, user=user, session=session, plan=plan, profile=profile, logs=logs,
            activities=pre_plan_acts, raw_message=user_message,
        )

    # 4. Appel agentique — outils filtrés par mode (spec 009 : pas d'outil plan en mode
    # libre, pas d'outil mode libre quand un plan est actif).
    from app.llm.tools import tools_for_mode
    from app.services.coaching_mode import mode_from_plan

    response_text, tool_used, last_tool_result, usage, tool_calls_log = await run_agentic_loop(
        system=system,
        messages=messages,
        tools=tools_for_mode(mode_from_plan(plan)),
        tool_executor=tool_executor,
    )

    # Voix demandée introuvable → on répond avec la voix par défaut et on le dit (FR-026).
    if _voice_fell_back and response_text:
        from app.services.coach_voice import VOICE_FALLBACK_NOTICE

        response_text = VOICE_FALLBACK_NOTICE + response_text

    # La suggestion mode libre (spec 009) enregistre son TSS cible dans le même registre
    # que CTL/ATL/TSB — un chiffre mal repris par le modèle est retiré comme n'importe
    # quelle autre métrique (spec 006 US3). Durée et zone ne s'enregistrent PAS : le
    # vérificateur les exclut déjà structurellement (durées/zones ne sont jamais des
    # affirmations, contracts/guardrails.md §3 / CLAUDE.md R5) — les enregistrer serait
    # mort code.
    if (
        tool_used == "get_freestyle_session_suggestion"
        and last_tool_result
        and last_tool_result.get("available")
    ):
        registry.register("tss", last_tool_result.get("target_tss"))

    # 4b. Vérification : chaque chiffre que la réponse avance sur une métrique doit
    # correspondre à ce qui a été retrouvé ; sinon la phrase est retirée et l'échec
    # enregistré (spec 006 US3, FR-017/FR-019/FR-021).
    try:
        verification = verify_response(response_text, registry)
        if not verification.ok:
            for claim, expected in verification.mismatches:
                await repo.guardrail_repo.record_check_failure(
                    session,
                    user_id=user.id,
                    failure_kind="mismatch",
                    metric_name=claim.metric,
                    stated_value=claim.stated_text,
                    expected_value=f"{expected:g}",
                    response_excerpt=claim.sentence,
                )
            for claim in verification.unretrieved:
                await repo.guardrail_repo.record_check_failure(
                    session,
                    user_id=user.id,
                    failure_kind="unretrieved",
                    metric_name=claim.metric,
                    stated_value=claim.stated_text,
                    expected_value=None,
                    response_excerpt=claim.sentence,
                )
            response_text = apply_result(response_text, verification)
    except Exception:
        logger.warning("Vérification de réponse impossible — réponse envoyée telle quelle")

    # 4c. Confirmation calorique déterministe — ce que le modèle DIT avoir enregistré
    # n'est pas fiable à 100% (texte libre, jamais vérifié comme les métriques d'entraînement
    # ci-dessus — cf. CLAUDE.md § Suivi calorique, "délibérément non fait"). Bug réel vu en
    # conditions réelles (2026-09-21) : un repas décrit en langage naturel n'entraînait
    # aucun appel `log_meal` tant que l'athlète ne redemandait pas explicitement, et un
    # second tour n'a loggé que 2 des 3 aliments sans que ce soit clair dans la réponse.
    # Décision utilisateur (2026-09-21) : PAS fusionné dans response_text — un message
    # Telegram séparé, façon trace de log, envoyé en plus de la réponse normale du coach.
    # `None` si aucun outil nutrition n'a tourné ce tour (le silence EST le signal —
    # rien n'apparaît quand rien n'a été écrit en base, au lieu d'un texte ambigu).
    meal_log = _format_meal_ledger(tool_calls_log)

    # 5. Déduire l'intent depuis l'outil appelé
    intent = _intent_from_tool(tool_used)

    # 6. Extraire la proposition de modification si applicable — spec 010 : une suggestion
    # mode libre disponible devient elle aussi une pending_proposal (bouton de
    # publication), au même titre qu'une modification de plan.
    pending_proposal: dict | None = None
    if (
        tool_used in ("propose_plan_modification", "propose_session_adjustment")
        and last_tool_result
        and "error" not in last_tool_result
        and "no_session" not in last_tool_result
    ):
        pending_proposal = last_tool_result
    elif (
        tool_used == "get_freestyle_session_suggestion"
        and last_tool_result
        and last_tool_result.get("available")
    ):
        pending_proposal = last_tool_result

    # 7. Accusé de réception d'un signal garde-fou (spec 006 US4, FR-024/FR-025).
    # Le garde-fou n'écrit rien lui-même : une acceptation passe par les outils
    # existants (propose_plan_modification → chemin d'approbation habituel). On
    # enregistre seulement la décision de l'athlète pour ne plus re-proposer le même
    # changement (occurrence_key).
    if guardrail_findings:
        try:
            if pending_proposal is not None:
                decision = "accepted"
            elif _looks_like_decline(user_message):
                decision = "declined"
            else:
                decision = None
            if decision is not None:
                for f in guardrail_findings:
                    if await repo.guardrail_repo.get_acknowledgement(
                        session, user.id, f.occurrence_key
                    ) is None:
                        await repo.guardrail_repo.record_acknowledgement(
                            session,
                            user_id=user.id,
                            finding_kind=f.kind,
                            occurrence_key=f.occurrence_key,
                            decision=decision,
                        )
        except Exception:
            logger.warning("Impossible d'enregistrer la décision garde-fou")

    return response_text, intent, tool_used, pending_proposal, usage, meal_log


_DECLINE_PHRASES = (
    "non merci", "non je continue", "je continue quand même", "laisse tomber",
    "pas maintenant", "pas cette fois", "je garde la séance", "je fais quand même",
    "ça ira", "je préfère garder", "je maintiens",
)


def _looks_like_decline(message: str) -> bool:
    """A deliberately narrow keyword check — only explicit refusals of a suggested
    change count as a decline (FR-025). Anything ambiguous is left unrecorded so the
    signal keeps being raised."""
    low = message.lower().strip()
    return any(p in low for p in _DECLINE_PHRASES)


async def _execute_tool(
    name: str,
    args: dict,
    user: User,
    session: AsyncSession,
    plan,
    profile: AthleteProfileSchema | None,
    logs: list,
    activities: list | None = None,
    raw_message: str = "",
) -> dict:
    """Dispatch vers la fonction déterministe correspondante."""

    if name == "get_upcoming_sessions":
        days = args.get("days", 7)
        return _tool_get_upcoming_sessions(plan, days)

    elif name == "update_injury_status":
        return await _tool_update_injury_status(
            args, user=user, session=session, plan=plan, profile=profile
        )

    elif name == "propose_plan_modification":
        return _tool_propose_plan_modification(args, plan=plan)

    elif name == "propose_session_adjustment":
        return _tool_propose_session_adjustment(args, plan=plan, profile=profile)

    elif name == "update_coach_memory":
        return await _tool_update_coach_memory(args, user=user, session=session)

    elif name == "get_freestyle_session_suggestion":
        return await _tool_get_freestyle_session_suggestion(
            args, user=user, session=session, profile=profile, logs=logs,
            activities=activities or [],
        )

    elif name == "log_meal":
        return await _tool_log_meal(args, user=user, session=session, raw_message=raw_message)

    elif name == "undo_last_meal_entry":
        return await _tool_undo_last_meal_entry(user=user, session=session)

    elif name == "get_calorie_history":
        return await _tool_get_calorie_history(args, user=user, session=session)

    elif name == "memory_query":
        return await _tool_memory_query(args, user=user, session=session)

    else:
        return {"error": f"Outil inconnu : {name}"}


# ── Implémentations des outils ────────────────────────────────────────────────

def _tool_get_upcoming_sessions(plan, days: int) -> dict:
    if plan is None:
        return {"sessions": [], "message": "Aucun plan actif trouvé."}
    if plan.start_date is None:
        return {"sessions": [], "message": "Le plan n'a pas encore de date de début."}

    from app.engine.schemas import TrainingPlanSchema
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)

    today = date.today()
    sessions = []
    for offset in range(days):
        target_date = today + timedelta(days=offset)
        week_num = (target_date - plan.start_date).days // 7 + 1
        dow = target_date.weekday()

        for week in schema.weeks:
            if week.week_number == week_num:
                for sess in week.sessions:
                    if sess.day_of_week == dow:
                        sessions.append({
                            "date": str(target_date),
                            "day": DAY_NAMES_FR[dow],
                            "workout_type": WORKOUT_FR.get(sess.workout_type, sess.workout_type),
                            "zone": sess.zone_code,
                            "duration_minutes": sess.duration_minutes,
                            "tss_target": round(sess.tss_target),
                            "description": sess.description_fr,
                            "week_number": week_num,
                            "phase": week.phase,
                        })
                break

    if not sessions:
        return {"sessions": [], "message": f"Aucune séance prévue dans les {days} prochains jours."}
    return {"sessions": sessions}


async def _tool_update_injury_status(
    args: dict,
    user: User,
    session,
    plan,
    profile: AthleteProfileSchema | None,
) -> dict:
    location = args.get("location", "other")
    severity = args.get("severity", "mild")
    recovery_days = args.get("estimated_recovery_days", 7)

    # Définir les restrictions de zones selon la sévérité
    zone_restrictions = _compute_zone_restrictions(severity)

    injury_data = {
        "is_injured": True,
        "location": location,
        "severity": severity,
        "zone_restrictions": zone_restrictions,
        "start_date": str(date.today()),
        "estimated_recovery_date": str(date.today() + timedelta(days=recovery_days)),
    }

    # Mettre à jour le profil en DB
    await repo.profile_repo.update_injury_status(session, user.id, injury_data)

    # Journal daté (Enduragent parity review, 2026-09-20) — texte templaté depuis les
    # arguments déjà contraints par les enum JSON-Schema de l'outil (location, severity),
    # jamais de prose libre du LLM (source="deterministic").
    from app.db.repositories import journal_repo
    from app.llm.tools import LOCATION_FR, SEVERITY_FR

    loc_fr = LOCATION_FR.get(location, location)
    sev_fr = SEVERITY_FR.get(severity, severity)
    await journal_repo.create(
        session,
        user_id=user.id,
        entry_date=date.today(),
        category="injury",
        source="deterministic",
        text=f"Blessure signalée : {loc_fr} ({sev_fr}), récupération estimée {recovery_days}j.",
    )

    # Adapter le plan si disponible
    adapted_weeks = []
    if plan and plan.start_date:
        from app.engine.plan_modifier import adapt_plan_for_injury
        adapted = adapt_plan_for_injury(plan, injury_data)
        adapted_weeks = adapted.get("modified_weeks", [])

    return {
        "success": True,
        "injury_recorded": injury_data,
        "zone_restrictions": zone_restrictions,
        "adapted_weeks": adapted_weeks,
        "message": f"Blessure ({location}, {severity}) enregistrée. Plan adapté pour {min(recovery_days, 21)} jours.",
    }


def _tool_propose_session_adjustment(args: dict, plan, profile: AthleteProfileSchema | None) -> dict:
    if plan is None:
        return {"error": "Aucun plan actif à modifier."}

    day_offset = args.get("day_offset", 0)
    action = args.get("action", "skip")
    target_date = date.today() + timedelta(days=day_offset)

    available_days = []
    if profile and profile.availability:
        available_days = profile.availability.preferred_days or []

    from app.engine.plan_modifier import propose_session_adjustment
    result = propose_session_adjustment(plan, target_date, action, available_days)
    if "error" not in result:
        result["constraint_type"] = args.get("constraint_type", "personal")
    return result


def _tool_propose_plan_modification(args: dict, plan) -> dict:
    if plan is None:
        return {"error": "Aucun plan actif à modifier."}

    reason = args.get("reason", "fatigue")
    modification_type = args.get("modification_type", "reduce_intensity")
    week_offset = args.get("week_offset", 0)

    from app.engine.plan_modifier import propose_week_adjustment
    proposal = propose_week_adjustment(plan, week_offset, modification_type)
    proposal["reason"] = reason

    return proposal


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _tool_update_coach_memory(args: dict, user: User, session: AsyncSession) -> dict:
    """Mémorise une observation durable dans coach_memory ou athlete_notes.

    add_note écrit à DEUX endroits en une seule opération (Enduragent parity review,
    2026-09-20, décision owner actée) : coach_memory (JSON capé à 15, court/moyen terme
    toujours injecté dans le system prompt — inchangé) ET coach_journal_entries
    (append-only, source="llm") — une note évincée au-delà de 15 reste queryable
    indéfiniment via l'outil memory_query au lieu d'être perdue. Le LLM ne décide
    toujours que du texte/catégorie, jamais de la mécanique de double écriture
    (Principe III)."""
    from datetime import date as _date
    from app.db.repositories import journal_repo, profile_repo

    action = args.get("action")
    profile_orm = await profile_repo.get_by_user_id(session, user.id)
    if profile_orm is None:
        return {"ok": False, "error": "Profil introuvable"}

    if action == "add_note":
        category = args.get("category", "preference")
        note_text = (args.get("note") or "")[:120]
        memory: list = list(profile_orm.coach_memory or [])

        if len(memory) >= 15:
            same_cat = [i for i, n in enumerate(memory) if n.get("category") == category]
            if same_cat:
                idx = min(same_cat, key=lambda i: memory[i].get("date", ""))
            else:
                idx = min(range(len(memory)), key=lambda i: memory[i].get("date", ""))
            memory[idx] = {"date": str(_date.today()), "category": category, "note": note_text}
        else:
            memory.append({"date": str(_date.today()), "category": category, "note": note_text})

        await profile_repo.update_coach_memory(session, profile_orm, memory)
        await journal_repo.create(
            session,
            user_id=user.id,
            entry_date=_date.today(),
            category=category,
            source="llm",
            text=note_text,
        )
        return {"ok": True, "action": "add_note", "category": category}

    elif action == "update_athlete_notes":
        key = args.get("key", "")
        value = args.get("value", "")
        if not key:
            return {"ok": False, "error": "key manquante"}
        notes: dict = dict(profile_orm.athlete_notes or {})
        notes[key] = value
        await profile_repo.update_athlete_notes(session, profile_orm, notes)
        return {"ok": True, "action": "update_athlete_notes", "key": key}

    return {"ok": False, "error": f"action inconnue : {action}"}


# ── Outil mode libre (spec 009) ─────────────────────────────────────────────────

async def _tool_get_freestyle_session_suggestion(
    args: dict,
    *,
    user: User,
    session: AsyncSession,
    profile: AthleteProfileSchema | None,
    logs: list,
    activities: list,
) -> dict:
    """Propose une séance sans référence à un plan (contracts/llm-tool-session-suggestion.md).
    Tout le calcul est déterministe (app/engine/freestyle_selector.py, Constitution
    Principe I) — cette fonction ne fait que rassembler ce que l'outil a besoin de lire
    en DB et traduit le résultat dans les deux formes du contrat.

    `args` (spec 011) porte les signaux extraits du message de l'athlète —
    `requested_workout_type`/`max_duration_minutes`/`style_preference`, tous optionnels.
    Les deux premiers sont transmis tels quels à `build_freestyle_suggestion()`. Le
    troisième (texte libre) est résolu en `template_id` par un second appel LLM isolé
    (`app/llm/template_picker.py`) qui ne voit que les candidats du type déjà retenu — le
    catalogue complet ne transite plus par le schéma de l'outil. Aucun calcul de charge
    n'a lieu ici (Constitution Principe III : llm/ ne calcule jamais)."""
    from datetime import date as _date

    from app.db.repositories import profile_repo
    from app.engine.atl_ctl import compute_fitness_from_any
    from app.engine.freestyle_selector import (
        NoSuitableTemplateError,
        build_freestyle_suggestion,
        candidates_for,
        choose_workout_type,
        days_since_hard_effort,
    )
    from app.engine.session_library import SessionLibraryError
    from app.engine.weekly_snapshot import compute_weekly_snapshot
    from app.llm.template_picker import pick_template
    from app.services.fitness import get_current_fitness

    today = _date.today()
    all_items = list(activities) + list(logs)

    current = await get_current_fitness(session, user.id, today=today)
    fitness = (
        current.metrics if current is not None
        else (compute_fitness_from_any(all_items) if all_items else None)
    )
    if fitness is None:
        return {
            "available": False,
            "reason": (
                "Pas encore assez de données de forme pour proposer une séance adaptée."
            ),
        }
    if profile is None:
        return {"available": False, "reason": "Profil athlète introuvable — lance /setup."}

    snapshot = compute_weekly_snapshot(all_items, today)
    hard_gap = days_since_hard_effort(all_items, today)

    profile_orm = await profile_repo.get_by_user_id(session, user.id)
    avoid_raw = (
        (profile_orm.athlete_notes or {}).get("disliked_workout_types", "")
        if profile_orm else ""
    )
    avoid_workout_types = frozenset(t.strip() for t in avoid_raw.split(",") if t.strip())
    requested_workout_type = args.get("requested_workout_type")

    template_id: str | None = None
    style_preference = (args.get("style_preference") or "").strip()
    if style_preference:
        # Resolve the workout type first so the picker only sees that type's candidates.
        # choose_workout_type() is pure and cheap; build_freestyle_suggestion() below
        # recomputes the same answer from the same inputs.
        try:
            choice = choose_workout_type(
                fitness, snapshot,
                days_since_hard_effort=hard_gap,
                avoid_workout_types=avoid_workout_types,
                requested_workout_type=requested_workout_type,
            )
            template_id = await pick_template(style_preference, candidates_for(choice.workout_type))
        except SessionLibraryError as exc:
            logger.warning("Préférence de style ignorée (bibliothèque) : %s", exc)

    try:
        suggestion = build_freestyle_suggestion(
            fitness,
            snapshot,
            coaching_mode=profile.coaching_mode,
            ftp=profile.equipment.ftp,
            days_since_hard_effort=hard_gap,
            avoid_workout_types=avoid_workout_types,
            day_ordinal=today.toordinal(),
            available_minutes=args.get("max_duration_minutes"),
            requested_workout_type=requested_workout_type,
            requested_template_id=template_id,
        )
    except (SessionLibraryError, NoSuitableTemplateError) as exc:
        logger.warning("Suggestion mode libre indisponible : %s", exc)
        return {
            "available": False,
            "reason": (
                "Impossible de trouver une séance adaptée dans la bibliothèque pour le moment."
            ),
        }

    # spec 010: tagged so app/bot/routers/chat.py can offer a "publish this" button —
    # "id" identifies exactly this suggestion so a later, superseded button can be told
    # apart from the current one (contracts/confirmation-button.md).
    return {
        "available": True,
        "type": "freestyle_publish",
        "id": uuid.uuid4().hex[:8],
        "workout_type": suggestion.workout_type,
        "duration_minutes": suggestion.duration_minutes,
        "target_tss": suggestion.target_tss,
        "zone_code": suggestion.zone_code,
        "reasoning_summary": suggestion.reasoning_summary,
        "steps": [step.model_dump(mode="json") for step in suggestion.steps],
    }


# ── Outils nutrition (spec 008) ────────────────────────────────────────────────

_MEAL_SLOT_LABELS_FR = {
    "breakfast": "petit-déjeuner",
    "lunch": "déjeuner",
    "dinner": "dîner",
    "snack": "collation",
    "other": "repas",
}


def _format_short_date(iso_date: str) -> str:
    """"2026-09-21" -> "21/09" — format court FR pour le message de confirmation."""
    try:
        return date.fromisoformat(iso_date).strftime("%d/%m")
    except (TypeError, ValueError):
        return iso_date


def _format_meal_ledger(tool_calls_log: list[dict]) -> str | None:
    """Confirmation déterministe de ce qui a RÉELLEMENT été écrit en base ce tour —
    construite depuis les résultats de `log_meal`/`undo_last_meal_entry`, jamais depuis
    le texte du modèle (qui peut décrire une entrée comme enregistrée sans qu'elle le
    soit, ou l'inverse — bug réel vu en conditions réelles, 2026-09-21).

    Destinée à un second message Telegram séparé, pas fusionnée à la réponse du coach
    (décision utilisateur) — format minimaliste choisi par l'utilisateur après plusieurs
    itérations : une ligne "✅ Enregistré" groupée par date, une ligne "🗑️ Supprimé" par
    annulation, une ligne "❌ Non enregistré" par échec, un total 🧾 par date concernée.
    `None` si aucun outil nutrition n'a tourné ce tour — l'absence du message EST le
    signal que rien n'a été écrit (le code n'a aucun moyen de savoir que le LLM *aurait
    dû* appeler l'outil et ne l'a pas fait — seule l'absence de message le trahit)."""
    from collections import defaultdict

    entries_by_date: dict[str, list[str]] = defaultdict(list)
    day_totals: dict[str, object] = {}
    removals: list[tuple[str, object]] = []
    failures: list[str] = []

    for call in tool_calls_log:
        name = call.get("name")
        if name not in ("log_meal", "undo_last_meal_entry"):
            continue
        args = call.get("args") or {}
        result = call.get("result") or {}

        if name == "log_meal":
            if result.get("ok"):
                cal = result.get("estimated_calories")
                entry_date = result.get("entry_date")
                if args.get("entry_type") == "day_recap":
                    label = "récap journée"
                else:
                    label = _MEAL_SLOT_LABELS_FR.get(args.get("meal_slot"), "repas")
                entries_by_date[entry_date].append(f"{label} ({cal} cal)")
                if result.get("day_total_estimated_calories") is not None:
                    day_totals[entry_date] = result.get("day_total_estimated_calories")
            else:
                failures.append(result.get("error", "erreur inconnue"))

        elif name == "undo_last_meal_entry":
            if result.get("ok"):
                entry_date = result.get("entry_date")
                removals.append((entry_date, result.get("removed_estimated_calories")))
                if result.get("day_total_estimated_calories") is not None:
                    day_totals[entry_date] = result.get("day_total_estimated_calories")
            else:
                failures.append(result.get("error", "erreur inconnue"))

    if not entries_by_date and not removals and not failures:
        return None

    lines: list[str] = []
    for items in entries_by_date.values():
        lines.append(f"✅ Enregistré — {', '.join(items)}")
    for _entry_date, cal in removals:
        lines.append(f"🗑️ Supprimé — -{cal} cal")
    for error in failures:
        lines.append(f"❌ Non enregistré — {error}")
    for entry_date, total in day_totals.items():
        lines.append(f"🧾 Total du {_format_short_date(entry_date)} : {total} cal")

    return "\n".join(lines)

_MEAL_DAYS_AGO_MAX = 2
_CALORIES_MIN = 1
_CALORIES_MAX = 8000


async def _tool_log_meal(args: dict, user: User, session: AsyncSession, raw_message: str) -> dict:
    """Enregistre un repas ou un récap de journée (contracts/nutrition-tools.md §1).
    L'estimation calorique vient du LLM (research R2 — hors du périmètre du Principe I,
    qui porte sur la charge d'entraînement) ; le total du jour, lui, est une somme SQL
    déterministe recalculée après insertion, jamais additionnée par le modèle."""
    from app.db.repositories import meal_entry_repo

    entry_type = args.get("entry_type")
    if entry_type not in ("meal", "day_recap"):
        entry_type = "meal"

    try:
        calories = round(float(args.get("estimated_calories")))
    except (TypeError, ValueError):
        calories = None
    if calories is None or not (_CALORIES_MIN <= calories <= _CALORIES_MAX):
        return {
            "ok": False,
            "error": "estimation calorique manquante ou hors limites plausibles — rien n'a été enregistré",
        }

    try:
        days_ago = max(0, min(int(args.get("days_ago", 0) or 0), _MEAL_DAYS_AGO_MAX))
    except (TypeError, ValueError):
        days_ago = 0
    entry_date = date.today() - timedelta(days=days_ago)

    meal_slot = args.get("meal_slot") if entry_type == "meal" else None

    # Un récap de journée REMPLACE les entrées déjà loggées ce jour-là plutôt que de s'y
    # ajouter — sinon la journée serait comptée deux fois (FR-009).
    replaced = False
    if entry_type == "day_recap":
        deleted_count = await meal_entry_repo.delete_for_date(session, user.id, entry_date)
        replaced = deleted_count > 0

    await meal_entry_repo.create(
        session,
        user_id=user.id,
        entry_date=entry_date,
        entry_type=entry_type,
        meal_slot=meal_slot,
        raw_description=raw_message[:500],
        estimated_calories=calories,
    )

    totals = await meal_entry_repo.daily_totals(session, user.id, entry_date, entry_date)
    day_total = totals[0].total_calories if totals else calories

    return {
        "ok": True,
        "estimated_calories": calories,
        "entry_date": str(entry_date),
        "day_total_estimated_calories": day_total,
        "replaced_existing_entries": replaced,
    }


async def _tool_undo_last_meal_entry(user: User, session: AsyncSession) -> dict:
    """Supprime la dernière entrée loggée AUJOURD'HUI (contracts §2) — jamais un autre
    jour, une correction est un geste dans la même session."""
    from app.db.repositories import meal_entry_repo

    today = date.today()
    latest = await meal_entry_repo.get_latest_for_date(session, user.id, today)
    if latest is None:
        return {"ok": False, "error": "aucune entrée aujourd'hui à annuler"}

    removed_calories = latest.estimated_calories
    await meal_entry_repo.delete(session, latest)

    totals = await meal_entry_repo.daily_totals(session, user.id, today, today)
    day_total = totals[0].total_calories if totals else 0

    return {
        "ok": True,
        "removed_estimated_calories": removed_calories,
        "entry_date": str(today),
        "day_total_estimated_calories": day_total,
    }


async def _tool_get_calorie_history(args: dict, user: User, session: AsyncSession) -> dict:
    """Historique jour par jour (contracts §3) — chaque jour de la période est présent,
    loggé ou non ; un jour non loggé n'a jamais de `total_calories` (FR-008)."""
    from app.db.repositories import meal_entry_repo

    try:
        days = max(1, min(int(args.get("days", 7) or 7), 30))
    except (TypeError, ValueError):
        days = 7

    today = date.today()
    start = today - timedelta(days=days - 1)
    totals = await meal_entry_repo.daily_totals(session, user.id, start, today)
    by_date = {t.entry_date: t for t in totals}

    result_days = []
    for offset in range(days):
        d = start + timedelta(days=offset)
        if d in by_date:
            t = by_date[d]
            result_days.append({
                "date": str(d),
                "logged": True,
                "total_calories": t.total_calories,
                "entry_count": t.entry_count,
            })
        else:
            result_days.append({"date": str(d), "logged": False})

    return {"range_start": str(start), "range_end": str(today), "days": result_days}


# ── Journal daté / memory_query (Enduragent parity review, 2026-09-20) ──────────

_MEMORY_QUERY_MAX_RANGE_DAYS = 400
_MEMORY_QUERY_LIMIT = 30
_MEMORY_QUERY_MAX_RESULT_CHARS = 3000


async def _tool_memory_query(args: dict, user: User, session: AsyncSession) -> dict:
    """Recherche dans le journal daté (app/db/models/coach_journal.py) — l'équivalent
    banister du memory_query d'Enduragent (packages/engine/src/sport/memory-tools.ts,
    vérifié sur le vrai source). Plafonds validés par l'owner : 400 jours de plage max,
    30 entrées / 3000 caractères de résultat avec `truncated: true` au-delà.

    Fencing DIFFÉRÉ par décision owner (2026-09-20) : seul sanitize_untrusted_text() est
    appliqué ici (niveau standard minimal, cohérent avec les autres outils du projet qui
    ne wrappent pas leurs résultats). TODO : réévaluer si un fencing complet
    (wrap_untrusted_block, comme build_system_prompt() l'applique à coach_memory) est
    nécessaire pour ce canal — une partie des entrées est écrite par le LLM lui-même
    (voie add_note) donc potentiellement adversariale, mais le canal `role="tool"` de la
    boucle agentique est structurellement distinct du system prompt.
    """
    from app.db.repositories import journal_repo
    from app.llm.prompt_fence import sanitize_untrusted_text

    try:
        start = date.fromisoformat(args.get("from_date", ""))
        end = date.fromisoformat(args.get("to_date", ""))
    except (TypeError, ValueError):
        return {"error": "from_date/to_date doivent être au format AAAA-MM-JJ."}

    if start > end:
        return {"error": f"from_date ({start}) est après to_date ({end}) — inverse les bornes."}

    range_days = (end - start).days + 1
    if range_days > _MEMORY_QUERY_MAX_RANGE_DAYS:
        return {
            "error": (
                f"Plage trop large ({range_days} jours, max "
                f"{_MEMORY_QUERY_MAX_RANGE_DAYS}) — réduis-la."
            ),
        }

    keyword = (args.get("keyword") or "").strip() or None
    category = args.get("category") or None

    rows = await journal_repo.query(
        session, user.id, start, end,
        keyword=keyword, category=category, limit=_MEMORY_QUERY_LIMIT,
    )

    truncated = len(rows) >= _MEMORY_QUERY_LIMIT
    entries: list[dict] = []
    total_chars = 0
    for row in rows:
        text = sanitize_untrusted_text(row.text)
        if total_chars + len(text) > _MEMORY_QUERY_MAX_RESULT_CHARS:
            truncated = True
            break
        total_chars += len(text)
        entries.append({"date": str(row.entry_date), "category": row.category, "text": text})

    return {
        "range": {"from": str(start), "to": str(end)},
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    }


def _compute_zone_restrictions(severity: str) -> dict:
    """Retourne les restrictions de zones selon la sévérité."""
    if severity == "severe":
        return {"Z3": "Z1", "Z4": "Z1", "Z5": "Z1", "Z6": "Z1"}
    elif severity == "moderate":
        return {"Z4": "Z2", "Z5": "Z2", "Z6": "Z2"}
    else:  # mild
        return {"Z5": "Z3", "Z6": "Z3"}


def _intent_from_tool(tool_used: str | None) -> str:
    mapping = {
        "update_injury_status": "injury_report",
        "propose_plan_modification": "plan_modification",
        "propose_session_adjustment": "plan_modification",
        "get_upcoming_sessions": "question",
    }
    return mapping.get(tool_used or "", "other")
