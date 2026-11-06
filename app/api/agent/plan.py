"""The training plan: reading it, and proposing changes the athlete approves."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import current_user, intervals_client
from app.db.client import get_session
from app.db.models.user import User
from app.providers.intervals.client import IntervalsClient
from app.services import agent_actions, plan_management
from app.services.plan_management import PlanChangeError

router = APIRouter(prefix="/plan")


def _fail(exc: PlanChangeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@router.get("")
async def plan(
    week: int | None = Query(None, ge=1),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await plan_management.plan_view(session, user, week=week)
    except PlanChangeError as exc:
        return _fail(exc)


class NewPlan(BaseModel):
    goal: Literal["event", "fitness", "performance", "other"]
    target_date: date | None = None


class WeekChange(BaseModel):
    reason: Literal["fatigue", "injury", "event", "preference", "illness"]
    modification_type: Literal[
        "reduce_intensity", "reduce_volume", "skip_session", "swap_to_recovery"
    ]
    week_offset: int = Field(ge=0, le=2)


class SessionChange(BaseModel):
    constraint_type: Literal["meeting", "weather_bad", "tired_today", "personal"]
    day_offset: int = Field(ge=0, le=6)
    action: Literal["skip", "shift", "reduce_50", "indoor"]


async def _propose(session: AsyncSession, call) -> dict | JSONResponse:
    try:
        result = await call
    except PlanChangeError as exc:
        return _fail(exc)
    await session.commit()
    return result


@router.post("/proposals/new", status_code=201)
async def propose_new_plan(
    body: NewPlan,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
):
    return await _propose(
        session, plan_management.propose_new_plan(session, user, body.goal, body.target_date, client)
    )


@router.post("/proposals/leave", status_code=201)
async def propose_leaving(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _propose(session, plan_management.propose_leaving_plan(session, user))


@router.post("/proposals/week", status_code=201)
async def propose_week(
    body: WeekChange,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _propose(
        session, plan_management.propose_week_change(session, user, **body.model_dump())
    )


@router.post("/proposals/session", status_code=201)
async def propose_session(
    body: SessionChange,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _propose(
        session, plan_management.propose_session_change(session, user, **body.model_dump())
    )


@router.post("/calendar/publish-request", status_code=201)
async def calendar_publish_request(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _propose(session, agent_actions.request_plan_publication(session, user))


@router.post("/calendar/withdraw-request")
async def calendar_withdraw_request(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
):
    return await _propose(session, agent_actions.request_plan_withdrawal(session, user))
