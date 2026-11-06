"""Finding, evaluating and proposing a single session."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import current_user
from app.core.time import paris_today
from app.db.client import get_session
from app.db.models.user import User
from app.engine.freestyle_selector import VALID_WORKOUT_TYPES
from app.services import session_design
from app.services.freestyle_suggestion import suggest_freestyle_session
from app.services.session_design import SessionDesignError, SessionDraft

router = APIRouter()


def _fail(exc: SessionDesignError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


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
