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
from app.llm.tools import TOOL_DEFINITIONS, build_context_messages, build_system_prompt
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
) -> tuple[str, str | None, str | None, dict | None]:
    """
    Exécute le cycle de chat agentique pour un message utilisateur.

    Retourne (response_text, intent, tool_used_name).
    intent est déterminé a posteriori selon l'outil appelé.
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
    ux_rules = build_ux_system_prompt(user_level)
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
    except Exception:
        logger.warning("Impossible de calculer les signaux garde-fous")

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
    )
    system = f"{ux_rules}\n\n---\n\n{coaching_ctx}"
    if has_load_reduction_finding(guardrail_findings):
        from app.llm.prompts import GUARDRAIL_LOAD_REDUCTION_RULE

        system = f"{system}\n\n{GUARDRAIL_LOAD_REDUCTION_RULE}"

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
    try:
        from app.services.guardrail_service import collect_registry_metrics

        for _name, _value in (
            await collect_registry_metrics(session, user.id)
        ).items():
            registry.register(_name, _value)
    except Exception:
        logger.warning("Impossible de collecter les métriques garde-fous pour le registre")

    messages = build_context_messages(history)
    messages.append({"role": "user", "content": user_message})

    # 3. Définir le tool_executor (fermeture sur session/plan/profile)
    async def tool_executor(name: str, args: dict) -> dict:
        return await _execute_tool(name, args, user=user, session=session, plan=plan, profile=profile, logs=logs, activities=pre_plan_acts)

    # 4. Appel agentique
    response_text, tool_used, last_tool_result = await run_agentic_loop(
        system=system,
        messages=messages,
        tools=TOOL_DEFINITIONS,
        tool_executor=tool_executor,
    )

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

    # 6. Extraire la proposition de modification si applicable
    pending_proposal: dict | None = None
    if (
        tool_used in ("propose_plan_modification", "propose_session_adjustment")
        and last_tool_result
        and "error" not in last_tool_result
        and "no_session" not in last_tool_result
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

    return response_text, intent, tool_used, pending_proposal


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

    else:
        return {"error": f"Outil inconnu : {name}"}


# ── Implémentations des outils ────────────────────────────────────────────────

def _tool_get_fitness_data(logs: list, activities: list) -> dict:
    all_items = activities + logs
    if not all_items:
        return {"atl": 0, "ctl": 0, "tsb": 0, "tsb_label": "Pas de données", "recent_logs": []}

    from app.engine.atl_ctl import compute_fitness_from_any, tsb_label
    metrics = compute_fitness_from_any(all_items)
    label = tsb_label(metrics.tsb)

    # 7 items les plus récents (SessionLog ou Activity, duck-typed)
    def _date(it):
        return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)

    def _tss(it):
        return getattr(it, "tss_actual", None) or getattr(it, "tss", None)

    recent = []
    for it in sorted(all_items, key=_date)[-7:]:
        recent.append({
            "date": str(_date(it)),
            "tss": _tss(it),
            "source": "plan" if hasattr(it, "rpe_emoji") else "intervals_icu",
        })

    return {
        "atl": round(metrics.atl, 1),
        "ctl": round(metrics.ctl, 1),
        "tsb": round(metrics.tsb, 1),
        "tsb_label": label,
        "recent_sessions": recent,
    }


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
    """Mémorise une observation durable dans coach_memory ou athlete_notes."""
    from datetime import date as _date
    from app.db.repositories import profile_repo

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
        "get_fitness_data": "question",
        "get_upcoming_sessions": "question",
    }
    return mapping.get(tool_used or "", "other")
