"""Post-activity context assembly (spec 002 T044, FR-038).

Provider-agnostic and aiogram-free: takes an already-mapped `AnalyzedSession` and
assembles everything a post-activity notification needs — plan matching, the
`SessionLog` row, fitness state, weekly snapshot, highlight selection, KPI contribution
— so the whole sequence is testable without simulating a Telegram conversation
(tests/test_services/test_activity_feedback.py).

Built during the cutover (spec 002 Phase 6) as a standalone replacement for the
now-deleted inbound webhook handler's non-Telegram logic — same business rule, relocated
and made provider-agnostic (FR-033: matching's behaviour is explicitly unchanged).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.engine.adherence_kpi import compute_session_kpi
from app.engine.atl_ctl import (
    FitnessMetrics,
    compute_fitness_from_any,
    estimate_initial_ctl,
    tsb_label,
)
from app.engine.schemas import TrainingPlanSchema
from app.engine.tss import tss_from_weekly_hours
from app.services.fitness import get_current_fitness
from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot
from app.providers.analysis.analysis_models import AnalyzedSession
from app.providers.analysis.highlight import HighlightResult, select_highlight
from app.providers.analysis.matching import ActivitySessionMatch, evaluate_activity_plan_match

Outcome = Literal["matched", "freestyle", "unplanned", "bonus"]


@dataclass
class ActivityFeedbackContext:
    """Everything the staged notification needs to render. `outcome` tells the caller
    which of the four notification shapes applies — training outside the plan (or with
    no plan at all, "freestyle", spec 009) is never presented as an error (FR-034)."""

    outcome: Outcome
    analyzed: AnalyzedSession
    log: SessionLog | None = None
    match_result: ActivitySessionMatch | None = None
    fitness_metrics: FitnessMetrics | None = None
    fitness_feedback: str = ""
    weekly_snapshot: WeeklySnapshot | None = None
    highlight: HighlightResult | None = None
    kpi_contribution: float | None = None
    reason: str | None = None
    reason_details: list[str] = field(default_factory=list)


async def assemble_activity_feedback(
    session: AsyncSession,
    user: User,
    initial_analyzed: AnalyzedSession,
    activity_date: date,
    *,
    source: str,
    source_activity_id: str,
    reanalyze_with_plan: Callable[[str, int], AnalyzedSession] | None = None,
) -> ActivityFeedbackContext:
    """`reanalyze_with_plan(planned_zone, planned_target_time_in_zone_s)` lets the
    caller re-derive `AnalyzedSession` once a matching planned session is known, so
    `respect_zones_score` can be computed against it — provider-specific (intervals.icu:
    a cheap local re-map of the same payload). Omit it to skip that refinement and use
    `initial_analyzed` as final.
    """
    plan = await repo.plan_repo.get_active_plan(session, user.id)

    # Idempotency guard: a prior call for this same activity may have already created
    # the SessionLog but never got marked reported (e.g. Telegram delivery failed after
    # ingestion succeeded — exactly the case FR-012 exists to make retryable). Reuse
    # that row rather than creating a duplicate; everything else below (matching,
    # fitness, highlight) is cheap, pure computation and safe to simply redo so the
    # caller has what it needs to retry the notification. Checked before the plan==None
    # branch so a freestyle-mode retry is idempotent too (spec 009).
    existing_log = await repo.session_log_repo.get_by_source_activity(session, source_activity_id)
    if existing_log is not None and existing_log.user_id == user.id:
        return await _reuse_existing_log(
            session, user, plan, existing_log, initial_analyzed, activity_date
        )

    if plan is None:
        # spec 009 US3 — freestyle mode: nothing to match against, but the activity
        # still gets logged and gets feedback (FR-008), unlike the old silent "no_plan"
        # skip (SessionLog.plan_id used to be NOT NULL — see research.md Decision 3).
        return await _assemble_freestyle_feedback(
            session, user, initial_analyzed, activity_date,
            source=source, source_activity_id=source_activity_id,
        )

    all_logs_for_plan = await repo.session_log_repo.get_all_for_user(session, user.id)
    used_slots = frozenset(
        (log.week_number, log.day_of_week)
        for log in all_logs_for_plan
        if str(log.plan_id) == str(plan.id) and log.status == "done"
    )

    match_result = evaluate_activity_plan_match(
        plan, initial_analyzed, activity_date, used_slots=used_slots
    )

    duration_s = initial_analyzed.moving_time_s or initial_analyzed.duration_s
    elapsed_minutes = int(duration_s / 60)

    if match_result.candidate is None or not match_result.is_aligned:
        week_num = max(1, (activity_date - plan.start_date).days // 7 + 1)
        dow = activity_date.weekday()
        log_unplanned = await repo.session_log_repo.create(
            session=session,
            user_id=user.id,
            plan_id=plan.id,
            week_number=week_num,
            day_of_week=dow,
            logged_date=activity_date,
            status="unplanned",
            duration_minutes_actual=elapsed_minutes,
            tss_actual=initial_analyzed.tss,
            source_activity_id=source_activity_id,
            source=source,
            avg_heart_rate=int(initial_analyzed.avg_hr) if initial_analyzed.avg_hr else None,
            avg_power=int(initial_analyzed.avg_power) if initial_analyzed.avg_power else None,
            normalized_power=(
                int(initial_analyzed.normalized_power)
                if initial_analyzed.normalized_power
                else None
            ),
            environment=initial_analyzed.environment or "outdoor",
            time_in_zones_s=initial_analyzed.time_in_zones_s or None,
            cardiac_drift_index=initial_analyzed.cardiac_drift_index,
            efficiency_factor=initial_analyzed.efficiency_factor,
            hrr=initial_analyzed.hrr,
            rpe_emoji=initial_analyzed.rpe_emoji,
            intervals_consistency_index=initial_analyzed.intervals_consistency_index,
            respect_zones_score=initial_analyzed.respect_zones_score,
            session_type_real=initial_analyzed.session_type_real,
            variability_index=initial_analyzed.variability_index,
            intensity_factor=initial_analyzed.intensity_factor,
            dominant_zone=initial_analyzed.dominant_zone,
        )
        fitness_metrics, fitness_feedback = await _build_fitness_feedback(session, user.id)
        if fitness_metrics is not None:
            log_unplanned.ctl_at_session = round(fitness_metrics.ctl, 1)
            log_unplanned.atl_at_session = round(fitness_metrics.atl, 1)
            log_unplanned.tsb_at_session = round(fitness_metrics.tsb, 1)

        if match_result.candidate is None:
            outcome: Outcome = "bonus" if match_result.all_slots_taken else "unplanned"
            reason = (
                None
                if outcome == "bonus"
                else "Aucune séance planifiée à ±2 jours de cette date."
            )
            return ActivityFeedbackContext(
                outcome=outcome,
                analyzed=initial_analyzed,
                log=log_unplanned,
                fitness_metrics=fitness_metrics,
                fitness_feedback=fitness_feedback,
                reason=reason,
            )

        score = match_result.score
        score_label = f"{score.match_level} ({score.confidence_score}/100)" if score else "none"
        return ActivityFeedbackContext(
            outcome="unplanned",
            analyzed=initial_analyzed,
            log=log_unplanned,
            match_result=match_result,
            fitness_metrics=fitness_metrics,
            fitness_feedback=fitness_feedback,
            reason=f"Activité peu alignée avec la séance prévue (score {score_label}).",
            reason_details=list(score.reasons) if score else [],
        )

    candidate = match_result.candidate
    session_spec = candidate.session_spec
    analyzed = initial_analyzed

    if reanalyze_with_plan is not None and session_spec.target_time_in_zone_minutes:
        analyzed = reanalyze_with_plan(
            session_spec.zone_code, session_spec.target_time_in_zone_minutes * 60
        )

    log = await repo.session_log_repo.create(
        session=session,
        user_id=user.id,
        plan_id=plan.id,
        week_number=candidate.week_number,
        day_of_week=candidate.day_of_week,
        logged_date=activity_date,
        status="done",
        rpe_emoji=analyzed.rpe_emoji,
        duration_minutes_actual=elapsed_minutes,
        tss_actual=analyzed.tss,
        source_activity_id=source_activity_id,
        source=source,
        avg_heart_rate=int(analyzed.avg_hr) if analyzed.avg_hr else None,
        avg_power=int(analyzed.avg_power) if analyzed.avg_power else None,
        normalized_power=int(analyzed.normalized_power) if analyzed.normalized_power else None,
        environment=analyzed.environment or "outdoor",
        time_in_zones_s=analyzed.time_in_zones_s or None,
        cardiac_drift_index=analyzed.cardiac_drift_index,
        efficiency_factor=analyzed.efficiency_factor,
        hrr=analyzed.hrr,
        intervals_consistency_index=analyzed.intervals_consistency_index,
        respect_zones_score=analyzed.respect_zones_score,
        session_type_real=analyzed.session_type_real,
        variability_index=analyzed.variability_index,
        intensity_factor=analyzed.intensity_factor,
        dominant_zone=analyzed.dominant_zone,
    )

    fitness_metrics, fitness_feedback = await _build_fitness_feedback(session, user.id)
    if fitness_metrics is not None:
        log.ctl_at_session = round(fitness_metrics.ctl, 1)
        log.atl_at_session = round(fitness_metrics.atl, 1)
        log.tsb_at_session = round(fitness_metrics.tsb, 1)

    plan_schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    profile = await repo.profile_repo.get_by_user_id(session, user.id)
    profile_data = profile.profile if profile else {}
    level = profile_data.get("level", "intermediate")
    kpi_week = next(
        (w for w in plan_schema.weeks if w.week_number == candidate.week_number), None
    )
    kpi_contribution = None
    if kpi_week:
        kpi = compute_session_kpi(
            tss_planned=session_spec.tss_target,
            week_tss_planned=kpi_week.total_tss_target,
            weeks_total=plan_schema.weeks_count,
            tss_actual=analyzed.tss,
            workout_type=session_spec.workout_type,
            session_type_real=analyzed.session_type_real,
            tsb_after=fitness_metrics.tsb if fitness_metrics else None,
            level=level,
        )
        log.kpi_contribution = kpi.pts
        kpi_contribution = kpi.pts

    session_logs_snap = await repo.session_log_repo.get_all_for_user(session, user.id)
    activities_snap = await repo.activity_repo.get_for_user(session, user.id, days=42)
    weekly_snap = compute_weekly_snapshot(session_logs_snap + activities_snap, activity_date)
    fm = fitness_metrics or FitnessMetrics(ctl=0.0, atl=0.0, tsb=0.0)
    highlight = select_highlight(analyzed, fm, weekly_snap)

    return ActivityFeedbackContext(
        outcome="matched",
        analyzed=analyzed,
        log=log,
        match_result=match_result,
        fitness_metrics=fitness_metrics,
        fitness_feedback=fitness_feedback,
        weekly_snapshot=weekly_snap,
        highlight=highlight,
        kpi_contribution=kpi_contribution,
    )


async def _reuse_existing_log(
    session: AsyncSession,
    user: User,
    plan,
    existing_log: SessionLog,
    analyzed: AnalyzedSession,
    activity_date: date,
) -> ActivityFeedbackContext:
    """Rebuilds a context around an already-ingested log, for a retry after a previous
    delivery attempt failed (see the idempotency guard in assemble_activity_feedback).
    Recomputes matching/fitness/highlight fresh rather than trusting stale fields on the
    log — cheap, deterministic, and avoids a second schema for "what a retry needs"."""
    fitness_metrics, fitness_feedback = await _build_fitness_feedback(session, user.id)

    if existing_log.status != "done":
        # existing_log.plan_id is None exactly for a freestyle-mode log (spec 009) —
        # was dead code before plan_id became nullable, since every log had one.
        outcome: Outcome = "freestyle" if existing_log.plan_id is None else "bonus"
        return ActivityFeedbackContext(
            outcome=outcome,
            analyzed=analyzed,
            log=existing_log,
            fitness_metrics=fitness_metrics,
            fitness_feedback=fitness_feedback,
        )

    # A "done" log always came from a real match against `plan` — but the athlete may
    # have switched to freestyle mode (deactivating that plan) in the window between
    # ingestion and this retry. Never call evaluate_activity_plan_match(None, ...).
    match_result = evaluate_activity_plan_match(plan, analyzed, activity_date) if plan else None

    session_logs_snap = await repo.session_log_repo.get_all_for_user(session, user.id)
    activities_snap = await repo.activity_repo.get_for_user(session, user.id, days=42)
    weekly_snap = compute_weekly_snapshot(session_logs_snap + activities_snap, activity_date)
    fm = fitness_metrics or FitnessMetrics(ctl=0.0, atl=0.0, tsb=0.0)
    highlight = select_highlight(analyzed, fm, weekly_snap)

    return ActivityFeedbackContext(
        outcome="matched",
        analyzed=analyzed,
        log=existing_log,
        match_result=match_result if match_result and match_result.candidate else None,
        fitness_metrics=fitness_metrics,
        fitness_feedback=fitness_feedback,
        weekly_snapshot=weekly_snap,
        highlight=highlight,
        kpi_contribution=existing_log.kpi_contribution,
    )


async def _assemble_freestyle_feedback(
    session: AsyncSession,
    user: User,
    analyzed: AnalyzedSession,
    activity_date: date,
    *,
    source: str,
    source_activity_id: str,
) -> ActivityFeedbackContext:
    """spec 009 US3 — the freestyle-mode counterpart to the plan-matched path above.
    No `evaluate_activity_plan_match` at all (there is no plan to compare against);
    otherwise mirrors the "unplanned" branch's SessionLog shape (`plan_id`/`week_number`/
    `day_of_week` all `None`, `status="unplanned"` — data-model.md's exact reuse of the
    existing status vocabulary) and the "matched" branch's fitness/highlight assembly, so
    a freestyle notification looks and feels like any other, per FR-008/US3."""
    duration_s = analyzed.moving_time_s or analyzed.duration_s
    elapsed_minutes = int(duration_s / 60)

    log = await repo.session_log_repo.create(
        session=session,
        user_id=user.id,
        plan_id=None,
        week_number=None,
        day_of_week=None,
        logged_date=activity_date,
        status="unplanned",
        rpe_emoji=analyzed.rpe_emoji,
        duration_minutes_actual=elapsed_minutes,
        tss_actual=analyzed.tss,
        source_activity_id=source_activity_id,
        source=source,
        avg_heart_rate=int(analyzed.avg_hr) if analyzed.avg_hr else None,
        avg_power=int(analyzed.avg_power) if analyzed.avg_power else None,
        normalized_power=int(analyzed.normalized_power) if analyzed.normalized_power else None,
        environment=analyzed.environment or "outdoor",
        time_in_zones_s=analyzed.time_in_zones_s or None,
        cardiac_drift_index=analyzed.cardiac_drift_index,
        efficiency_factor=analyzed.efficiency_factor,
        hrr=analyzed.hrr,
        intervals_consistency_index=analyzed.intervals_consistency_index,
        respect_zones_score=analyzed.respect_zones_score,
        session_type_real=analyzed.session_type_real,
        variability_index=analyzed.variability_index,
        intensity_factor=analyzed.intensity_factor,
        dominant_zone=analyzed.dominant_zone,
    )

    fitness_metrics, fitness_feedback = await _build_fitness_feedback(session, user.id)
    if fitness_metrics is not None:
        log.ctl_at_session = round(fitness_metrics.ctl, 1)
        log.atl_at_session = round(fitness_metrics.atl, 1)
        log.tsb_at_session = round(fitness_metrics.tsb, 1)

    session_logs_snap = await repo.session_log_repo.get_all_for_user(session, user.id)
    activities_snap = await repo.activity_repo.get_for_user(session, user.id, days=42)
    weekly_snap = compute_weekly_snapshot(session_logs_snap + activities_snap, activity_date)
    fm = fitness_metrics or FitnessMetrics(ctl=0.0, atl=0.0, tsb=0.0)
    highlight = select_highlight(analyzed, fm, weekly_snap)

    return ActivityFeedbackContext(
        outcome="freestyle",
        analyzed=analyzed,
        log=log,
        fitness_metrics=fitness_metrics,
        fitness_feedback=fitness_feedback,
        weekly_snapshot=weekly_snap,
        highlight=highlight,
    )


async def _build_fitness_feedback(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[FitnessMetrics | None, str]:
    """Same fitness-state computation used by the (now-removed) inbound webhook path."""
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    plan_start = plan.start_date if plan else date.today()

    activities = await repo.activity_repo.get_for_user(session, user_id, days=365)
    pre_plan_acts = [a for a in activities if a.activity_date < plan_start]
    logs = await repo.session_log_repo.get_all_for_user(session, user_id)

    all_items = pre_plan_acts + logs
    if not all_items:
        return None, "📊 Données de forme indisponibles pour le moment."

    current = await get_current_fitness(session, user_id)
    if current is not None:
        metrics = current.metrics
    else:
        initial_ctl = await _estimate_ctl_seed(all_items, session, user_id)
        seed_date = (date.today() - timedelta(days=49)) if initial_ctl > 0 else None
        metrics = compute_fitness_from_any(all_items, initial_ctl=initial_ctl, seed_date=seed_date)
    label = tsb_label(metrics.tsb)
    text = (
        "📊 <b>Impact forme après cette séance</b>\n"
        f"CTL {metrics.ctl:.0f} · ATL {metrics.atl:.0f} · TSB {metrics.tsb:+.0f}\n"
        f"{label}"
    )
    return metrics, text


def _item_date(it):
    return getattr(it, "logged_date", None) or getattr(it, "activity_date", None)


async def _estimate_ctl_seed(items: list, session: AsyncSession, user_id: uuid.UUID) -> float:
    if not items:
        return 0.0

    oldest = min(_item_date(it) for it in items)
    span_days = (date.today() - oldest).days
    if span_days >= 84:
        return 0.0

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
