"""Agent API: lets an external agent (Hermes plugin, MCP server, your own) coach with Banister.

The agent understands the athlete and chooses or composes; Banister computes, checks and, once
the athlete approves, publishes. Contract: docs/AGENT_INTEGRATION.md.

Bound to the same local port as the rest of the app and never routed publicly. It has its own
bearer token, separate from `INTERNAL_API_TOKEN` because it can write. No token configured
means the whole router answers 404 (fail closed), so a default install exposes nothing.

`/approve` and `DELETE /publications` act on the athlete's calendar: they exist for the
athlete's own gesture (a button handled by the integration's code) and MUST NOT be offered to
the model as tools.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.time import paris_today
from app.db import repositories as repo
from app.db.client import get_session
from app.db.models.user import User
from app.engine.freestyle_selector import VALID_WORKOUT_TYPES
from app.providers.intervals.client import IntervalsClient
from app.services import coach_memory, coach_queries, session_design
from app.services.agent_verification import verify_agent_reply
from app.services.freestyle_suggestion import suggest_freestyle_session
from app.services.session_design import SessionDesignError, SessionDraft
from app.services.sport_context import MAX_AHEAD, MAX_DAYS, build_sport_context


def require_agent_token(authorization: str = Header(default="")) -> None:
    expected = settings.agent_api_token.get_secret_value()
    if not expected:
        raise HTTPException(404)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(401)


router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_agent_token)])

Granularity = Literal["daily", "weekly"]


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = await repo.user_repo.get_single_user(session)
    if user is None:
        raise HTTPException(404, "no athlete configured")
    return user


def intervals_client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(), athlete_id=settings.intervals_athlete_id
    )


def _fail(exc: SessionDesignError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


# ── Reading ──────────────────────────────────────────────────────────────────


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
        raise HTTPException(
            422, f"unknown metrics {unknown}: {sorted(coach_queries.WELLNESS_METRICS)}"
        )
    return await coach_queries.wellness_history(
        session, user.id, days=days, metrics=metrics, granularity=granularity
    )


@router.get("/rides/{ride_date}")
async def ride(
    ride_date: date,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    if ride_date > paris_today():
        raise HTTPException(422, "this date is in the future: no ride data")
    return await coach_queries.session_detail(session, user.id, session_date=ride_date)


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


# ── Finding a session ────────────────────────────────────────────────────────


@router.get("/sessions/catalog")
async def catalog(
    workout_type: str | None = None,
    family: str | None = None,
    phase: str | None = None,
    q: str | None = None,
    max_minutes: int | None = Query(None, ge=15, le=600),
    detail: bool = False,
) -> dict:
    templates = session_design.session_catalog(
        workout_type=workout_type,
        family=family,
        phase=phase,
        query=q,
        max_minutes=max_minutes,
        detail=detail,
    )
    return {"count": len(templates), "templates": templates}


@router.get("/sessions/recommendation")
async def recommendation(
    workout_type: str | None = None,
    max_minutes: int | None = Query(None, ge=15, le=300),
    duration_minutes: int | None = Query(None, ge=15, le=300),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """The engine's own pick from today's state: a reference, not an obligation."""
    if workout_type and workout_type not in VALID_WORKOUT_TYPES:
        raise HTTPException(422, f"workout_type must be one of {sorted(VALID_WORKOUT_TYPES)}")
    today = paris_today()
    try:
        profile = await session_design.load_profile(session, user)
    except SessionDesignError as exc:
        return _fail(exc)
    logs, activities = await session_design.load_recent_training(session, user, today)
    return await suggest_freestyle_session(
        {
            "requested_workout_type": workout_type,
            "max_duration_minutes": max_minutes,
            "requested_duration_minutes": duration_minutes,
        },
        user=user,
        session=session,
        profile=profile,
        logs=logs,
        activities=activities,
        today=today,
    )


@router.post("/sessions/evaluate")
async def evaluate(
    draft: SessionDraft,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await session_design.evaluate_session(session, user, draft)
    except SessionDesignError as exc:
        return _fail(exc)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


# ── Proposing and publishing ─────────────────────────────────────────────────


@router.post("/proposals", status_code=201)
async def propose(
    draft: SessionDraft,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        result = await session_design.create_proposal(session, user, draft)
    except SessionDesignError as exc:
        return _fail(exc)
    await session.commit()
    return result


@router.get("/proposals/{proposal_id}")
async def proposal(
    proposal_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await session_design.get_proposal(session, user, proposal_id)
    except SessionDesignError as exc:
        return _fail(exc)


@router.post("/proposals/{proposal_id}/approve")
async def approve(
    proposal_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    try:
        return await session_design.approve_proposal(session, user, proposal_id, client)
    except SessionDesignError as exc:
        return _fail(exc)


@router.post("/proposals/{proposal_id}/decline")
async def decline(
    proposal_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await session_design.decline_proposal(session, user, proposal_id)
    except SessionDesignError as exc:
        return _fail(exc)


@router.get("/publications")
async def publications(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return {"publications": await session_design.list_publications(session, user)}


@router.get("/publications/{publication_id}/withdrawal-card")
async def withdrawal_card(
    publication_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await session_design.withdrawal_card(session, user, publication_id)
    except SessionDesignError as exc:
        return _fail(exc)


@router.delete("/publications/{publication_id}")
async def withdraw(
    publication_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    try:
        return await session_design.withdraw_publication(session, user, publication_id, client)
    except SessionDesignError as exc:
        return _fail(exc)


# ── Checking a reply ─────────────────────────────────────────────────────────


class ReplyCheck(BaseModel):
    text: str = Field(max_length=20000)
    retrieved: list[dict] = Field(default_factory=list, max_length=100)


@router.post("/verify-reply")
async def verify_reply(
    body: ReplyCheck,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await verify_agent_reply(session, user, body.text, body.retrieved)
