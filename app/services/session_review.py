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

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.engine.atl_ctl import FitnessMetrics
from app.engine.schemas import SessionSpec, TrainingPlanSchema, session_at
from app.engine.tid import TIDResult, compute_tid
from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot


@dataclass
class ReviewContext:
    log: SessionLog
    session_spec: SessionSpec | None  # séance planifiée correspondante, si liée à un plan
    # Snapshot CTL/ATL/TSB au moment loggé — autoritaire, jamais recalculé.
    fitness_at_session: FitnessMetrics | None
    weekly_snapshot: WeeklySnapshot  # tendance/monotonie autour de la date de la séance
    tid: TIDResult | None  # TID/polarisation 7j autour de la séance (2026-09-21)


async def assemble_review_context(
    session: AsyncSession, user: User, log: SessionLog
) -> ReviewContext:
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

    return ReviewContext(
        log=log,
        session_spec=session_spec,
        fitness_at_session=fitness_at_session,
        weekly_snapshot=weekly_snapshot,
        tid=tid,
    )
