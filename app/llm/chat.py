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
import re
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.core.time import paris_today
from app.db import repositories as repo
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics, compute_fitness_from_any
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.engine.training_history import completed_training_items
from app.llm.chat_client import run_agentic_loop
from app.llm.tools import PARALLEL_READ_TOOLS, build_context_messages, build_system_prompt
from app.services import nutrition_source
from app.services.coach_memory import (
    _MEMORY_QUERY_LIMIT,  # noqa: F401 — re-exported for tests
    _MEMORY_QUERY_MAX_RANGE_DAYS,  # noqa: F401
    _MEMORY_QUERY_MAX_RESULT_CHARS,  # noqa: F401
)
from app.services.coach_memory import memory_query as _tool_memory_query
from app.services.coach_memory import update_coach_memory as _tool_update_coach_memory
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)

# Athlete free text may be in either language regardless of APP_LANGUAGE (R6) — this
# claim detector must catch both, not just the installation language.
_ACTION_CLAIM_RE = re.compile(
    r"\b(?:c['’]est|ça a été|cela a été|je l['’]ai|je viens de|it['’]s|it has been|"
    r"i['’]ve|i just)\s+"
    r"(?:bien\s+|just\s+)?(?:noté|enregistré|ajouté|modifié|supprimé|publié|"
    r"noted|saved|logged|added|changed|updated|deleted|removed|published)\b",
    re.IGNORECASE,
)
_OTHER_ACTION_CLAIM_RE = re.compile(
    r"\b(?:je|nous|i|we)\s+(?:(?:le|la|les|ça|cela|it|this)\s+)?"
    r"(?:note|enregistre|ajoute|modifie|supprime|save|log|add|update|delete)\b|"
    r"\b[\wÀ-ÿ'-]+\s+(?:noté|enregistré|ajouté|modifié|supprimé)"
    r"(?:e|s|es)?\b",
    re.IGNORECASE,
)
_WRITE_TOOLS = frozenset({
    "update_injury_status", "update_coach_memory",
})


def _verify_action_claim(response_text: str, tool_calls_log: list[dict]) -> str:
    """A conversational claim cannot substitute for an executed tool."""
    wrote = any(
        call.get("name") in _WRITE_TOOLS
        and not (call.get("result") or {}).get("error")
        and ((call.get("result") or {}).get("ok") is True
             or (call.get("result") or {}).get("success") is True)
        for call in tool_calls_log
    )
    if not wrote and (
        _ACTION_CLAIM_RE.search(response_text) or _OTHER_ACTION_CLAIM_RE.search(response_text)
    ):
        return t("llm.chat.no_action_executed" if tool_calls_log else "llm.chat.no_tool_executed")
    return response_text


async def run_chat(
    user_message: str,
    user: User,
    session: AsyncSession,
    reply_context: str | None = None,
    on_tool_event=None,
) -> tuple[str, str | None, str | None, dict | None, dict]:
    """
    Exécute le cycle de chat agentique pour un message utilisateur.

    Retourne (response_text, intent, tool_used_name, pending_proposal, usage).
    intent est déterminé a posteriori selon l'outil appelé. `usage` (tokens
    prompt/completion/total, nombre d'appels API) sert à mesurer le coût réel d'un tour
    de chat — voir app/services/token_usage.py et scripts/token_usage_state.py.

    `reply_context` : texte du message Telegram auquel l'athlète a répondu en reply-quote
    (n'importe quel message du bot — chat, /review, /recap, notification — puisque
    Telegram renvoie ce texte brut dans le payload, indépendamment de ce que Banister a
    lui-même loggé dans `chat_messages`). `None` si le message n'est pas une réponse.
    """
    # 1. Charger le contexte
    today = paris_today()
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    context_start = today - timedelta(days=89)
    logs = await repo.session_log_repo.get_in_range(
        session, user.id, context_start, today
    )
    history = await repo.chat_repo.get_conversation(session, user.id, limit=8)

    # Activités importées pré-plan (sans double-comptage avec session_logs)
    activities = await repo.activity_repo.get_in_range(session, user.id, context_start, today)
    all_items = completed_training_items([*logs, *activities], start=context_start, end=today)

    profile: AthleteProfileSchema | None = None
    if profile_row and profile_row.profile:
        try:
            profile = AthleteProfileSchema.model_validate(profile_row.profile)
        except Exception:
            logger.warning("Impossible de valider le profil athlète")

    current = await get_current_fitness(session, user.id, today=today)
    fitness_as_of = current.as_of if current is not None else None
    fitness_is_stale = current.is_stale if current is not None else False
    metrics: FitnessMetrics | None = (
        current.metrics if current is not None
        else (compute_fitness_from_any(all_items, target_date=today) if all_items else None)
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
        user_level, persona=_persona,
        first_name=user.first_name or t("llm.chat.default_athlete_name"),
    )
    def _item_date(item):
        return item.logged_date if hasattr(item, "logged_date") else item.activity_date

    all_recent_items = sorted(all_items, key=_item_date)
    recent_items = all_recent_items[-1:]
    from app.services.coach_queries import hot_training_summary

    training_summary = hot_training_summary(all_recent_items, today=today)
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
        _today = today
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
    latest_weight = None
    try:
        wellness_today = await repo.wellness_repo.get_by_date(session, user.id, today)
        latest_weight = await repo.wellness_repo.get_latest_weight(
            session, user.id, on_or_before=today
        )
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
            _wk_num = (today - plan_schema.start_date).days // 7 + 1
            _cur_week = next((w for w in plan_schema.weeks if w.week_number == _wk_num), None)
            if _cur_week:
                _plan_week_phase = _cur_week.phase
        _target_date = profile.objective.target_date if profile else None
        detected_phase_result = detect_training_phase(
            all_items,
            today=today,
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

        registry_metrics = await collect_registry_metrics(session, user.id, today=today)
    except Exception:
        logger.warning("Impossible de collecter les métriques garde-fous pour le registre")

    coaching_ctx = build_system_prompt(
        first_name=user.first_name or t("llm.chat.default_athlete_name"),
        profile=profile,
        metrics=metrics,
        fitness_as_of=fitness_as_of,
        fitness_is_stale=fitness_is_stale,
        recent_logs=recent_items,
        plan=plan_schema,
        today=today,
        session_logs=logs,  # permet d'afficher plan + réalisé pour la semaine en cours
        coach_memory=list(profile_row.coach_memory or []) if profile_row else None,
        athlete_notes=dict(profile_row.athlete_notes or {}) if profile_row else None,
        calendar_divergence=calendar_divergence,
        guardrail_findings=guardrail_findings,
        recovery_insufficiency=recovery_gap,
        wellness_today=wellness_today,
        latest_weight=latest_weight,
        recovery_index=registry_metrics.get("recovery_index"),
        detected_phase=detected_phase_result,
        training_summary=training_summary,
    )
    system = f"{ux_rules}\n\n---\n\n{coaching_ctx}"
    if has_load_reduction_finding(guardrail_findings):
        from app.llm.prompts import guardrail_load_reduction_rule

        system = f"{system}\n\n{guardrail_load_reduction_rule()}"

    # spec 010 : réinjectée en entier à chaque tour (contrairement à l'historique, qui ne
    # rejoue jamais les appels d'outils passés — trouvé en live, voir prompts.py).
    if plan is None:
        from app.llm.prompts import freestyle_session_tool_rule

        system = f"{system}\n\n{freestyle_session_tool_rule()}"

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
        turn_content = t(
            "llm.chat.reply_context_wrapper", context=reply_context, message=user_message
        )
    messages.append({"role": "user", "content": turn_content})

    # 3. Définir le tool_executor (fermeture sur session/plan/profile)
    async def tool_executor(name: str, args: dict) -> dict:
        # SQLAlchemy AsyncSession is not safe for concurrent use. Read-only calls in
        # one model wave receive independent sessions; all other calls retain the
        # request session to preserve mutation order and transaction visibility.
        if name in PARALLEL_READ_TOOLS:
            from app.db.client import AsyncSessionFactory

            async with AsyncSessionFactory() as read_session:
                return await _execute_tool(
                    name, args, user=user, session=read_session, plan=plan, profile=profile,
                    logs=logs,
                    activities=activities, today=today,
                )
        return await _execute_tool(
            name, args, user=user, session=session, plan=plan, profile=profile, logs=logs,
            activities=activities, today=today,
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
        parallel_tool_names=PARALLEL_READ_TOOLS,
        on_tool_event=on_tool_event,
    )
    response_text = _verify_action_claim(response_text, tool_calls_log)

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

    # History tools expose several valid values for the same metric. Register them
    # before verification, without replacing current values from the hot context.
    for call in tool_calls_log:
        result = call.get("result") or {}
        name = call.get("name")
        if name == "get_fitness_history":
            for metric in ("ctl", "atl", "tsb", "ramp_rate"):
                values = {
                    point["date"]: point.get(metric)
                    for point in result.get("points", [])
                    if point.get("date")
                }
                registry.register_history(metric, values)
        elif name == "get_training_trend":
            values = {
                week["week_start"]: week.get("tss")
                for week in result.get("weeks", [])
                if week.get("week_start")
            }
            registry.register_history("tss", values)
        elif name == "get_wellness_history":
            hrv_values = {
                point["date"]: point.get("hrv")
                for point in result.get("points", [])
                if point.get("date")
            }
            rhr_values = {
                point["date"]: point.get("resting_hr")
                for point in result.get("points", [])
                if point.get("date")
            }
            registry.register_history("hrv", hrv_values)
            registry.register_history("rhr", rhr_values)
        elif name == "get_session_detail":
            as_of = result.get("date", "")
            values = {as_of: item.get("tss") for item in result.get("sessions", [])}
            registry.register_history("tss", values)

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

    return response_text, intent, tool_used, pending_proposal, usage


# Athlete free text may be in either language regardless of APP_LANGUAGE (R6) — checked
# together, not switched on the installation language.
_DECLINE_PHRASES = (
    "non merci", "non je continue", "je continue quand même", "laisse tomber",
    "pas maintenant", "pas cette fois", "je garde la séance", "je fais quand même",
    "ça ira", "je préfère garder", "je maintiens",
    "no thanks", "no i'll continue", "i'll do it anyway", "never mind",
    "not now", "not this time", "i'll keep the session", "i'm doing it anyway",
    "i'm fine", "i'd rather keep", "i'll stick with it",
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
    today: date | None = None,
) -> dict:
    """Dispatch vers la fonction déterministe correspondante."""

    if name == "get_upcoming_sessions":
        days = args.get("days", 7)
        start_offset = args.get("start_offset", 0)
        return _tool_get_upcoming_sessions(plan, days, start_offset)

    elif name == "get_fitness_history":
        return await _tool_get_fitness_history(args, user=user, session=session)

    elif name == "get_training_trend":
        return await _tool_get_training_trend(args, user=user, session=session)

    elif name == "get_session_detail":
        return await _tool_get_session_detail(args, user=user, session=session)

    elif name == "get_wellness_history":
        return await _tool_get_wellness_history(args, user=user, session=session)

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
            activities=activities or [], today=today,
        )

    elif name == "get_nutrition_summary":
        return await _tool_get_nutrition_summary(args, user=user, session=session)

    elif name == "memory_query":
        return await _tool_memory_query(args, user=user, session=session)

    else:
        return {"error": f"Outil inconnu : {name}"}


# ── Implémentations des outils ────────────────────────────────────────────────

_DAY_KEYS = [
    "day.full.monday", "day.full.tuesday", "day.full.wednesday", "day.full.thursday",
    "day.full.friday", "day.full.saturday", "day.full.sunday",
]


def _tool_get_upcoming_sessions(plan, days: int, start_offset: int = 0) -> dict:
    from app.llm.narrator import workout_label

    if plan is None:
        return {"sessions": [], "message": t("llm.tools.no_active_plan")}
    if plan.start_date is None:
        return {"sessions": [], "message": t("llm.tools.plan_no_start_date")}

    from app.engine.schemas import TrainingPlanSchema
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)

    try:
        days = max(1, min(int(days), 42))
        start_offset = max(-7, min(int(start_offset), 56))
    except (TypeError, ValueError):
        days, start_offset = 7, 0

    start = paris_today() + timedelta(days=start_offset)
    sessions = []
    for offset in range(days):
        target_date = start + timedelta(days=offset)
        week_num = (target_date - plan.start_date).days // 7 + 1
        dow = target_date.weekday()

        for week in schema.weeks:
            if week.week_number == week_num:
                for sess in week.sessions:
                    if sess.day_of_week == dow:
                        sessions.append({
                            "date": str(target_date),
                            "day": t(_DAY_KEYS[dow]),
                            "workout_type": workout_label(sess.workout_type),
                            "zone": sess.zone_code,
                            "duration_minutes": sess.duration_minutes,
                            "tss_target": round(sess.tss_target),
                            "description": sess.description_fr,
                            "week_number": week_num,
                            "phase": week.phase,
                        })
                break

    if not sessions:
        return {
            "range_start": str(start),
            "range_end": str(start + timedelta(days=days - 1)),
            "sessions": [],
            "message": t("llm.tools.no_sessions_in_window", days=days),
        }
    return {
        "range_start": str(start),
        "range_end": str(start + timedelta(days=days - 1)),
        "sessions": sessions,
    }


async def _tool_get_fitness_history(args: dict, *, user: User, session: AsyncSession) -> dict:
    from app.services.coach_queries import clamp_days, fitness_history

    days = clamp_days(args.get("days"), default=28, maximum=365)
    granularity = args.get("granularity")
    if granularity not in {"daily", "weekly"}:
        granularity = "weekly" if days > 30 else "daily"
    if granularity == "daily" and days > 90:
        return {"error": t("llm.tools.daily_history_limited")}
    return await fitness_history(session, user.id, days=days, granularity=granularity)


async def _tool_get_training_trend(args: dict, *, user: User, session: AsyncSession) -> dict:
    from app.services.coach_queries import clamp_days, training_trend

    days = clamp_days(args.get("days"), default=28, maximum=90)
    return await training_trend(session, user.id, days=days)


async def _tool_get_session_detail(args: dict, *, user: User, session: AsyncSession) -> dict:
    from app.services.coach_queries import session_detail

    try:
        session_date = date.fromisoformat(args.get("date", ""))
    except (TypeError, ValueError):
        return {"error": t("llm.tools.date_format_error")}
    if session_date > paris_today():
        return {"error": t("llm.tools.future_session_no_data")}
    return await session_detail(session, user.id, session_date=session_date)


async def _tool_get_wellness_history(args: dict, *, user: User, session: AsyncSession) -> dict:
    from app.services.coach_queries import clamp_days, wellness_history

    days = clamp_days(args.get("days"), default=28, maximum=90)
    granularity = args.get("granularity")
    if granularity not in {"daily", "weekly"}:
        granularity = "weekly" if days > 30 else "daily"
    metrics = args.get("metrics")
    if not isinstance(metrics, list):
        metrics = []
    return await wellness_history(
        session, user.id, days=days, metrics=metrics, granularity=granularity
    )


async def _tool_update_injury_status(
    args: dict,
    user: User,
    session,
    plan,
    profile: AthleteProfileSchema | None,
) -> dict:
    from app.services.athlete_status import update_injury_status

    return await update_injury_status(args, user, session, plan)


def _tool_propose_session_adjustment(
    args: dict, plan, profile: AthleteProfileSchema | None,
) -> dict:
    if plan is None:
        return {"error": t("llm.tools.no_plan_to_modify")}

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
        return {"error": t("llm.tools.no_plan_to_modify")}

    reason = args.get("reason", "fatigue")
    modification_type = args.get("modification_type", "reduce_intensity")
    week_offset = args.get("week_offset", 0)

    from app.engine.plan_modifier import propose_week_adjustment
    proposal = propose_week_adjustment(plan, week_offset, modification_type)
    proposal["reason"] = reason

    return proposal


# ── Helpers ───────────────────────────────────────────────────────────────────

# ── Outil mode libre (spec 009) ─────────────────────────────────────────────────

async def _tool_get_freestyle_session_suggestion(
    args: dict,
    *,
    user: User,
    session: AsyncSession,
    profile: AthleteProfileSchema | None,
    logs: list,
    activities: list,
    today: date | None = None,
) -> dict:
    """Same-turn suggestion for the chat. The logic lives in
    `app/services/freestyle_suggestion.py`; only the LLM style picker is added here, which
    is why it stays out of the service (Constitution Principle III)."""
    from app.llm.template_picker import pick_template
    from app.services.freestyle_suggestion import suggest_freestyle_session

    return await suggest_freestyle_session(
        args,
        user=user,
        session=session,
        profile=profile,
        logs=logs,
        activities=activities,
        today=today or paris_today(),
        resolve_style=pick_template,
    )


# ── Nutrition : lecture seule d'une source externe (docs/NUTRITION_INTEGRATION.md) ──
# Banister ne stocke ni n'estime aucun repas. Il lit, quand ça éclaire le vélo.


async def _tool_get_nutrition_summary(args: dict, user: User, session: AsyncSession) -> dict:
    """Apports récents, ou `available: false` si la source ne répond pas : rien d'inventé."""
    try:
        days = max(1, min(int(args.get("days", 7) or 7), 30))
    except (TypeError, ValueError):
        days = 7
    return await nutrition_source.daily_summary(session, user.id, days)


def _intent_from_tool(tool_used: str | None) -> str:
    mapping = {
        "update_injury_status": "injury_report",
        "propose_plan_modification": "plan_modification",
        "propose_session_adjustment": "plan_modification",
        "get_upcoming_sessions": "question",
    }
    return mapping.get(tool_used or "", "other")
