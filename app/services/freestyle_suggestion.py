"""The engine's freestyle session recommendation, without any bot or LLM dependency.

Used by the built-in chat tool and by the agent API. All the numbers come from
`app/engine/freestyle_selector.py` (Constitution Principle I); this module only gathers
what the selector needs to read from the database and shapes its answer.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.localization import t
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics
from app.engine.schemas import AthleteProfileSchema
from app.engine.training_history import completed_training_items, daily_training_tss
from app.engine.weekly_snapshot import WeeklySnapshot
from app.services.fitness import CurrentFitness

logger = logging.getLogger(__name__)

StyleResolver = Callable[[str, list], Awaitable[str | None]]


@dataclass(frozen=True)
class FreestyleInputs:
    """Everything the selector reads about the athlete's current state."""

    fitness: FitnessMetrics
    current: CurrentFitness | None
    snapshot: WeeklySnapshot
    all_items: list
    days_since_hard_effort: int | None
    days_since_return_from_break: int | None
    recent_daily_tss: float | None
    acwr_finding_kind: str | None
    recovery_finding_kind: str | None
    recovery_gap: str | None
    avoid_workout_types: frozenset[str]


async def gather_freestyle_inputs(
    *,
    user: User,
    session: AsyncSession,
    logs: list,
    activities: list,
    today: date,
) -> FreestyleInputs | None:
    """`None` when there is no fitness figure at all, measured or estimated."""
    from app.db.repositories import profile_repo
    from app.engine.atl_ctl import compute_fitness_from_any
    from app.engine.freestyle_selector import days_since_hard_effort, days_since_return_from_break
    from app.engine.weekly_snapshot import compute_weekly_snapshot
    from app.services.fitness import get_current_fitness
    from app.services.guardrail_service import (
        assemble_recovery_findings,
        assemble_workload_findings,
        recovery_insufficiency,
    )

    all_items = completed_training_items([*logs, *activities], end=today)

    current = await get_current_fitness(session, user.id, today=today)
    fitness = (
        current.metrics
        if current is not None
        else (compute_fitness_from_any(all_items, target_date=today) if all_items else None)
    )
    if fitness is None:
        return None

    daily_tss = daily_training_tss(all_items, start=today - timedelta(days=1), end=today)
    # Binding on the freestyle side (2026-09-21): an exclusion inside the deterministic
    # selector, not advice in the prompt. See choose_workout_type().
    acwr_finding_kind: str | None = None
    try:
        workload_findings = await assemble_workload_findings(session, user.id, today=today)
        acwr_finding_kind = next(
            (f.kind for f in workload_findings if f.kind.startswith("acwr_")), None
        )
    except Exception:
        logger.warning("Impossible de calculer l'ACWR pour la suggestion freestyle")

    recovery_finding_kind = None
    recovery_gap = None
    try:
        recovery_findings = await assemble_recovery_findings(session, user.id, today=today)
        recovery_finding_kind = next((f.kind for f in recovery_findings), None)
        if not recovery_findings:
            recovery_gap = await recovery_insufficiency(session, user.id, today=today)
    except Exception:
        logger.warning("Impossible de calculer la récupération pour la suggestion freestyle")
        recovery_gap = "Signaux de récupération indisponibles."

    profile_orm = await profile_repo.get_by_user_id(session, user.id)
    avoid_raw = (
        (profile_orm.athlete_notes or {}).get("disliked_workout_types", "") if profile_orm else ""
    )
    return FreestyleInputs(
        fitness=fitness,
        current=current,
        snapshot=compute_weekly_snapshot(all_items, today),
        all_items=all_items,
        days_since_hard_effort=days_since_hard_effort(all_items, today),
        days_since_return_from_break=days_since_return_from_break(all_items, today),
        recent_daily_tss=max(daily_tss.values(), default=None),
        acwr_finding_kind=acwr_finding_kind,
        recovery_finding_kind=recovery_finding_kind,
        recovery_gap=recovery_gap,
        avoid_workout_types=frozenset(x.strip() for x in avoid_raw.split(",") if x.strip()),
    )


async def suggest_freestyle_session(
    args: dict,
    *,
    user: User,
    session: AsyncSession,
    profile: AthleteProfileSchema | None,
    logs: list,
    activities: list,
    today: date,
    resolve_style: StyleResolver | None = None,
) -> dict:
    """Propose one session from the athlete's current state.

    `args` carries the optional signals `requested_workout_type`, `max_duration_minutes`,
    `requested_duration_minutes` and `style_preference`. Free-text style is only resolved
    when the caller supplies `resolve_style`; no load calculation happens there.
    """
    from app.engine.freestyle_selector import (
        NoSuitableTemplateError,
        build_freestyle_suggestion,
        candidates_for,
        choose_workout_type,
    )
    from app.engine.session_library import SessionLibraryError

    inputs = await gather_freestyle_inputs(
        user=user, session=session, logs=logs, activities=activities, today=today
    )
    if inputs is None:
        return {"available": False, "reason": t("llm.tools.freestyle_not_enough_data")}
    if profile is None:
        return {"available": False, "reason": t("llm.tools.freestyle_no_profile")}

    requested_workout_type = args.get("requested_workout_type")
    template_id: str | None = None
    style_preference = (args.get("style_preference") or "").strip()
    if style_preference and resolve_style is not None:
        # Resolve the workout type first so the picker only sees that type's candidates.
        # choose_workout_type() is pure and cheap; build_freestyle_suggestion() below
        # recomputes the same answer from the same inputs.
        try:
            choice = choose_workout_type(
                inputs.fitness,
                inputs.snapshot,
                days_since_hard_effort=inputs.days_since_hard_effort,
                avoid_workout_types=inputs.avoid_workout_types,
                requested_workout_type=requested_workout_type,
                days_since_return_from_break=inputs.days_since_return_from_break,
                acwr_finding_kind=inputs.acwr_finding_kind,
                recent_daily_tss=inputs.recent_daily_tss,
                recovery_finding_kind=inputs.recovery_finding_kind,
            )
            template_id = await resolve_style(style_preference, candidates_for(choice.workout_type))
        except SessionLibraryError as exc:
            logger.warning("Préférence de style ignorée (bibliothèque) : %s", exc)

    try:
        suggestion = build_freestyle_suggestion(
            inputs.fitness,
            inputs.snapshot,
            coaching_mode=profile.coaching_mode,
            ftp=profile.equipment.ftp,
            days_since_hard_effort=inputs.days_since_hard_effort,
            avoid_workout_types=inputs.avoid_workout_types,
            day_ordinal=today.toordinal(),
            available_minutes=args.get("max_duration_minutes"),
            requested_duration_minutes=args.get("requested_duration_minutes"),
            requested_workout_type=requested_workout_type,
            requested_template_id=template_id,
            days_since_return_from_break=inputs.days_since_return_from_break,
            acwr_finding_kind=inputs.acwr_finding_kind,
            recent_daily_tss=inputs.recent_daily_tss,
            recovery_finding_kind=inputs.recovery_finding_kind,
        )
    except (SessionLibraryError, NoSuitableTemplateError) as exc:
        logger.warning("Suggestion mode libre indisponible : %s", exc)
        return {"available": False, "reason": t("llm.tools.freestyle_no_suitable_session")}

    # spec 010: tagged so app/bot/routers/chat.py can offer a "publish this" button — "id"
    # identifies exactly this suggestion so a superseded button can be told apart.
    current = inputs.current
    reasoning = suggestion.reasoning_summary
    if current is not None:
        reasoning += f" Forme source intervals.icu datée du {current.as_of.isoformat()}."
        if current.is_stale:
            reasoning += " Données de forme anciennes, pas une mesure actuelle."
    else:
        reasoning += " Forme estimée localement, faute de mesure source disponible."
    if inputs.recovery_gap:
        reasoning += f" Récupération non évaluable : {inputs.recovery_gap}"
    missing_tss = sum(
        1
        for item in inputs.all_items
        if (getattr(item, "tss_actual", None) is None and getattr(item, "tss", None) is None)
    )
    if missing_tss:
        reasoning += f" Charge incomplète : {missing_tss} sortie(s) sans TSS renseigné."
    return {
        "available": True,
        "type": "freestyle_publish",
        "id": uuid.uuid4().hex[:8],
        "workout_type": suggestion.workout_type,
        "template_id": suggestion.template_id,
        "duration_minutes": suggestion.duration_minutes,
        "target_tss": suggestion.target_tss,
        "zone_code": suggestion.zone_code,
        "reasoning_summary": reasoning,
        "fitness_as_of": current.as_of.isoformat() if current is not None else None,
        "fitness_is_stale": current.is_stale if current is not None else False,
        "fitness_source": "intervals.icu" if current is not None else "local_estimate",
        "duration_warning": suggestion.duration_warning,
        "steps": [step.model_dump(mode="json") for step in suggestion.steps],
    }
