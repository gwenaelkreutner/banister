"""Reading the athlete, their rides and memory; what they report about themselves."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import current_user, intervals_client
from app.core.time import paris_today
from app.db import repositories as repo
from app.db.client import get_session
from app.db.models.user import User
from app.engine.schemas import AthleteProfileSchema
from app.providers.intervals.client import IntervalsClient
from app.services import athlete_setup, athlete_status, coach_memory, coach_queries
from app.services.power_profile import fetch_power_profile
from app.services.session_review import (
    assemble_review_context,
    backfill_rpe_from_source,
    fetch_dfa,
    review_facts,
)
from app.services.sport_context import MAX_AHEAD, MAX_DAYS, build_sport_context
from app.services.weekly_recap import compute_weekly_recap

router = APIRouter()

Granularity = Literal["daily", "weekly"]


class Setup(BaseModel):
    hours_per_week: float = Field(gt=0, le=40)
    available_days: list[str]
    health_constraints: bool = False
    # Only what the source lacks, as `GET /setup` reports it.
    age: float | None = None
    max_hr: float | None = None
    resting_hr: float | None = None
    use_estimate: list[str] = Field(default_factory=list)
    owner_id: int | None = None


@router.get("/setup")
async def setup_status(
    session: AsyncSession = Depends(get_session),
    client: IntervalsClient = Depends(intervals_client),
):
    try:
        return await athlete_setup.setup_status(session, client)
    except athlete_setup.SetupError as exc:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@router.post("/setup", status_code=201)
async def setup(
    body: Setup,
    session: AsyncSession = Depends(get_session),
    client: IntervalsClient = Depends(intervals_client),
):
    values = {
        name: value
        for name, value in (
            ("age", body.age), ("max_hr", body.max_hr), ("resting_hr", body.resting_hr),
        )
        if value is not None
    }
    try:
        result = await athlete_setup.complete_setup(
            session,
            client,
            hours_per_week=body.hours_per_week,
            available_days=body.available_days,
            health_constraints=body.health_constraints,
            values=values,
            use_estimate=body.use_estimate,
            owner_id=body.owner_id,
        )
    except athlete_setup.SetupError as exc:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
    await session.commit()
    return result


class ProfileUpdate(BaseModel):
    hours_per_week: float | None = Field(default=None, gt=0, le=40)
    available_days: list[str] | None = None
    health_constraints: bool | None = None


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    try:
        result = await athlete_setup.update_profile(session, user, **body.model_dump())
    except athlete_setup.SetupError as exc:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
    await session.commit()
    return result


@router.get("/context")
async def context(
    days: int = Query(7, ge=1, le=MAX_DAYS),
    ahead: int = Query(7, ge=1, le=MAX_AHEAD),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await build_sport_context(session, user.id, days=days, ahead=ahead)


@router.get("/fitness")
async def fitness(
    days: int = Query(28, ge=7, le=365),
    granularity: Granularity = "daily",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    if granularity == "daily" and days > 90:
        raise HTTPException(422, "daily history is limited to 90 days: use weekly")
    return await coach_queries.fitness_history(session, user.id, days=days, granularity=granularity)


@router.get("/trend")
async def trend(
    days: int = Query(28, ge=7, le=90),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await coach_queries.training_trend(session, user.id, days=days)


@router.get("/wellness")
async def wellness(
    metrics: list[str] = Query(...),
    days: int = Query(28, ge=7, le=90),
    granularity: Granularity = "daily",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    unknown = sorted(set(metrics) - set(coach_queries.WELLNESS_METRICS))
    if unknown:
        valid = sorted(coach_queries.WELLNESS_METRICS)
        raise HTTPException(422, f"unknown metrics {unknown}: {valid}")
    return await coach_queries.wellness_history(
        session, user.id, days=days, metrics=metrics, granularity=granularity
    )


@router.get("/rides/{ride_date}")
async def ride(
    ride_date: date,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    """The day's rides, and for each logged one everything needed to review it."""
    if ride_date > paris_today():
        raise HTTPException(422, "this date is in the future: no ride data")
    detail = await coach_queries.session_detail(session, user.id, session_date=ride_date)
    logs = await repo.session_log_repo.get_by_date(session, user.id, ride_date)
    reviews = []
    for log in logs:
        await backfill_rpe_from_source(log, client)
        ctx = await assemble_review_context(session, user, log)
        reviews.append(review_facts(ctx, await fetch_dfa(log, client)))
    await session.commit()
    return {**detail, "reviews": reviews}


@router.get("/recap")
async def recap(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The week's figures: the same ones as the bot's recap, without its narration."""
    result = await compute_weekly_recap(session, user, narrate=False)
    await session.commit()
    return {
        "has_data": result.has_data,
        "text": result.stats_section,
        "tss_7d": result.tss_7d,
        "next_week_number": result.next_week_number,
    }


@router.get("/power-profile")
async def power_profile(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    profile = AthleteProfileSchema.model_validate(row.profile) if row and row.profile else None
    text = await fetch_power_profile(profile, client)
    return {"available": text is not None, "text": text}


# ── Memory ───────────────────────────────────────────────────────────────────


class Note(BaseModel):
    category: Literal["fatigue", "motivation", "physique", "event", "preference"]
    note: str = Field(min_length=1, max_length=120)


@router.get("/memory")
async def memory(
    keyword: str | None = None,
    category: str | None = None,
    days: int = Query(90, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    today = paris_today()
    notes = await repo.profile_repo.get_by_user_id(session, user.id)
    journal = await coach_memory.memory_query(
        {
            "from_date": (today - timedelta(days=days - 1)).isoformat(),
            "to_date": today.isoformat(),
            "keyword": keyword,
            "category": category,
        },
        user,
        session,
    )
    return {"athlete_notes": (notes.athlete_notes or {}) if notes else {}, "journal": journal}


@router.post("/memory/notes", status_code=201)
async def add_note(
    body: Note,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    result = await coach_memory.update_coach_memory(
        {"action": "add_note", "category": body.category, "note": body.note}, user, session
    )
    if not result.get("ok"):
        raise HTTPException(409, result.get("error"))
    await session.commit()
    return result


# ── What the athlete reports ─────────────────────────────────────────────────


class Injury(BaseModel):
    location: Literal["knee", "back", "shoulder", "hip", "ankle", "other"]
    severity: Literal["mild", "moderate", "severe"]
    estimated_recovery_days: int = Field(ge=1, le=90)


@router.put("/injury")
async def injury(
    body: Injury,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    if await repo.profile_repo.get_by_user_id(session, user.id) is None:
        raise HTTPException(409, "the athlete has no profile yet")
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    result = await athlete_status.update_injury_status(body.model_dump(), user, session, plan)
    await session.commit()
    return result


class Reminders(BaseModel):
    enabled: bool | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)


@router.get("/reminders")
async def get_reminders(user: User = Depends(current_user)) -> dict:
    return athlete_status.reminder_settings(user)


@router.put("/reminders")
async def put_reminders(
    body: Reminders,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    result = athlete_status.update_reminders(
        user, enabled=body.enabled, hour=body.hour, minute=body.minute
    )
    await session.commit()
    return result
