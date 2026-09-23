"""Assemblage déterministe du contexte pour `/review` — zéro LLM ici (Principe I).

Aiogram-free, comme app/services/activity_feedback.py, pour rester testable sans
simuler une conversation Telegram.

Écarté volontairement : réutiliser tel quel `assemble_activity_feedback()`. Cette
fonction est conçue pour un AnalyzedSession *frais* venant du provider juste après
ingestion (elle peut créer un log, gérer les cas "bonus"...) — la rejouer sur un
SessionLog déjà persisté impliquerait de reconstruire un faux AnalyzedSession et de
neutraliser des effets de bord qui n'ont pas lieu d'être pour une relecture a
posteriori. Les briques pures dont elle dépend, elles, sont réutilisées ici :
compute_weekly_snapshot() et session_at() (ce dernier remplace ~15 copies manuelles du
même lookup semaine/jour ailleurs dans le repo — voir app/engine/schemas.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics
from app.engine.phase_detection import PhaseDetectionResult, detect_training_phase
from app.engine.schemas import AthleteProfileSchema, SessionSpec, TrainingPlanSchema, session_at
from app.engine.tid import TIDResult, compute_tid
from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot
from app.services.guardrail_service import collect_registry_metrics


@dataclass
class ReviewContext:
    log: SessionLog
    session_spec: SessionSpec | None  # séance planifiée correspondante, si liée à un plan
    # Snapshot CTL/ATL/TSB au moment loggé — autoritaire, jamais recalculé.
    fitness_at_session: FitnessMetrics | None
    weekly_snapshot: WeeklySnapshot  # tendance/monotonie autour de la date de la séance
    tid: TIDResult | None  # TID/polarisation 7j autour de la séance (2026-09-21)
    # Le review porte sur cette sortie, pas sur le mode actif au moment où il est relu.
    # Un log sans plan est une sortie libre par construction (spec 009) : le LLM doit
    # le savoir pour ne jamais inventer une cible, une adhérence ou une prochaine séance
    # de programme.
    coaching_mode: Literal["goal", "freestyle"] = "freestyle"
    # recovery_index et detected_phase — recalculés À LA DATE DE LA SÉANCE (log.logged_date),
    # jamais "aujourd'hui" comme le chat (app/llm/chat.py) : ré-afficher l'état de
    # récupération/la phase d'AUJOURD'HUI sur la relecture d'une séance passée serait
    # trompeur. Les deux fonctions sources sont pures et déjà paramétrées par `today`,
    # donc réutilisables telles quelles ici (2026-09-21, voir CLAUDE.md § /review).
    recovery_index: float | None = None
    detected_phase: PhaseDetectionResult | None = None
    # Wellness du jour de la séance — hydratation/calories mangées telles que renseignées
    # sur intervals.icu (pas via notre suivi calorique chat, table séparée — voir
    # app/services/meal_entry_repo.py). Champs bruts déjà stockés depuis le chantier
    # "signaux enrichis intervals.icu" (2026-09-21) mais jamais montrés nulle part avant
    # ce câblage (2026-09-21, session suivante).
    hydration_volume_l: float | None = None
    kcal_consumed: int | None = None


async def assemble_review_context(
    session: AsyncSession, user: User, log: SessionLog
) -> ReviewContext:
    plan_schema: TrainingPlanSchema | None = None
    session_spec: SessionSpec | None = None
    if log.plan_id is not None and log.week_number is not None and log.day_of_week is not None:
        plan_row = await repo.plan_repo.get_by_id(session, log.plan_id)
        if plan_row is not None:
            plan_schema = TrainingPlanSchema.model_validate(plan_row.plan_technical)
            session_spec = session_at(plan_schema, log.week_number, log.day_of_week)

    fitness_at_session: FitnessMetrics | None = None
    if (
        log.ctl_at_session is not None
        and log.atl_at_session is not None
        and log.tsb_at_session is not None
    ):
        fitness_at_session = FitnessMetrics(
            ctl=log.ctl_at_session, atl=log.atl_at_session, tsb=log.tsb_at_session
        )

    all_logs = await repo.session_log_repo.get_all_for_user(session, user.id)
    weekly_snapshot = compute_weekly_snapshot(all_logs, log.logged_date)
    tid = compute_tid(all_logs, today=log.logged_date, window_days=7)

    registry_metrics = await collect_registry_metrics(session, user.id, today=log.logged_date)
    recovery_index = registry_metrics.get("recovery_index")

    # Recoupement du flux secondaire de detect_training_phase() : la phase déclarée par
    # le plan (si la séance en avait un) prime sur la proximité de date cible — même
    # priorité que chat.py, mais dérivée de log.week_number (déjà connu) plutôt que d'un
    # recalcul date/start_date, plus exact pour une séance passée.
    plan_week_phase: str | None = None
    if plan_schema is not None and log.week_number is not None:
        week = next((w for w in plan_schema.weeks if w.week_number == log.week_number), None)
        if week is not None:
            plan_week_phase = week.phase

    target_date = None
    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    if profile_row is not None:
        profile = AthleteProfileSchema.model_validate(profile_row.profile)
        target_date = profile.objective.target_date

    detected_phase = detect_training_phase(
        all_logs,
        today=log.logged_date,
        plan_week_phase=plan_week_phase,
        target_date=target_date,
    )

    wellness_at_session = await repo.wellness_repo.get_by_date(session, user.id, log.logged_date)
    hydration_volume_l = wellness_at_session.hydration_volume_l if wellness_at_session else None
    kcal_consumed = wellness_at_session.kcal_consumed if wellness_at_session else None

    return ReviewContext(
        log=log,
        session_spec=session_spec,
        fitness_at_session=fitness_at_session,
        weekly_snapshot=weekly_snapshot,
        tid=tid,
        coaching_mode="freestyle" if log.plan_id is None else "goal",
        recovery_index=recovery_index,
        detected_phase=detected_phase,
        hydration_volume_l=hydration_volume_l,
        kcal_consumed=kcal_consumed,
    )
