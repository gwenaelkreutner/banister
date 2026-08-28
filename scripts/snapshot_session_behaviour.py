"""spec 004 T001 — Captures matching and adherence-scoring results for every real
session_log against the athlete's real plan, so a before/after diff can prove SC-004
("Activity-to-session matching and adherence scoring produce byte-identical results
... before and after the change").

Re-runs the actual scoring functions rather than dumping stored historical values —
those were computed by whatever code existed at ingestion time, not necessarily the
code this snapshot is meant to compare against.

Usage: uv run python scripts/snapshot_session_behaviour.py --out <path.json>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from types import SimpleNamespace

sys.path.insert(0, ".")


def _build_analyzed_duck(log) -> SimpleNamespace | None:
    """A duck-typed stand-in for AnalyzedSession carrying only the attributes
    score_activity_vs_session() actually reads. Skips logs missing what matching
    needs — those were never scoreable activities in the first place."""
    if log.duration_minutes_actual is None:
        return None
    return SimpleNamespace(
        duration_s=log.duration_minutes_actual * 60,
        tss=log.tss_actual,
        session_type_real=log.session_type_real,
        dominant_zone=log.dominant_zone,
        environment=log.environment,
    )


async def main(out_path: str) -> None:
    from sqlalchemy import select

    from app.config import settings
    from app.db.client import AsyncSessionFactory
    from app.db import repositories as repo
    from app.db.models.training_plan import TrainingPlan
    from app.engine.schemas import TrainingPlanSchema
    from app.engine.adherence_kpi import compute_session_kpi
    from app.providers.analysis.matching import evaluate_activity_plan_match

    results: list[dict] = []

    async with AsyncSessionFactory() as session:
        user = await repo.user_repo.get_by_telegram_id(session, settings.telegram_owner_id)
        if user is None:
            print("No user found — writing an empty snapshot.")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"entries": []}, f, indent=2, sort_keys=True)
            return

        all_logs = await repo.session_log_repo.get_all_for_user(session, user.id)
        all_logs.sort(key=lambda lg: str(lg.id))  # stable ordering regardless of DB fetch order

        # Resolve each log against the plan it actually belongs to — not the
        # currently active one. A log's plan can be inactive (superseded by a
        # later /setup) without the log itself becoming meaningless; assuming
        # "active plan owns every log" broke this script the first time a real
        # /setup regeneration happened mid-session (spec 004, found live).
        plan_cache: dict[str, TrainingPlan | None] = {}

        for log in all_logs:
            plan_id = str(log.plan_id)
            if plan_id not in plan_cache:
                result = await session.execute(select(TrainingPlan).where(TrainingPlan.id == log.plan_id))
                plan_cache[plan_id] = result.scalar_one_or_none()
            plan = plan_cache[plan_id]

            entry: dict = {
                "log_id": str(log.id),
                "status": log.status,
                "week": log.week_number,
                "day": log.day_of_week,
            }

            if plan is None:
                entry["error"] = "plan referenced by this log no longer exists"
                results.append(entry)
                continue

            plan_schema = TrainingPlanSchema.model_validate(plan.plan_technical)
            weeks_by_num = {w.week_number: w for w in plan_schema.weeks}

            # used_slots mirrors app/services/activity_feedback.py's real
            # ingestion-time computation, scoped to this log's own plan.
            used_slots = frozenset(
                (lg.week_number, lg.day_of_week)
                for lg in all_logs
                if lg.plan_id == log.plan_id and lg.status == "done"
            )

            duck = _build_analyzed_duck(log)
            if duck is not None:
                match_result = evaluate_activity_plan_match(
                    plan, duck, log.logged_date, used_slots=used_slots
                )
                if match_result.candidate is not None:
                    entry["match"] = {
                        "candidate_week": match_result.candidate.week_number,
                        "candidate_day": match_result.candidate.day_of_week,
                        "confidence_score": match_result.score.confidence_score,
                        "match_level": match_result.score.match_level,
                        "reasons": match_result.score.reasons,
                    }
                else:
                    entry["match"] = {
                        "candidate": None,
                        "all_slots_taken": match_result.all_slots_taken,
                    }

            # Adherence is only meaningful for a session actually matched to a plan slot.
            week = weeks_by_num.get(log.week_number)
            spec = (
                next((s for s in week.sessions if s.day_of_week == log.day_of_week), None)
                if week else None
            )
            if week is not None and spec is not None and log.status == "done":
                kpi = compute_session_kpi(
                    tss_planned=spec.tss_target,
                    week_tss_planned=week.total_tss_target,
                    weeks_total=plan_schema.weeks_count,
                    tss_actual=log.tss_actual,
                    workout_type=spec.workout_type,
                    session_type_real=log.session_type_real,
                    tsb_after=log.tsb_at_session,
                )
                entry["kpi"] = asdict(kpi)

            results.append(entry)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"entries": results}, f, indent=2, sort_keys=True, ensure_ascii=False)

    print(f"Wrote {len(results)} entries to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.out))
