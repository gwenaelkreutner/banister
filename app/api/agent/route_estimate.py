"""Moving-time estimate for a route the athlete wants to ride (allure-cycling)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import current_user, intervals_client
from app.db import repositories as repo
from app.db.client import get_session
from app.db.models.user import User
from app.engine.schemas import AthleteProfileSchema
from app.providers.intervals.client import IntervalsClient
from app.services.route_estimate import RouteEstimateError, RouteRequest, estimate_route

router = APIRouter()

MAX_GPX_CHARS = 8_000_000


class RouteEstimateBody(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    gpx: str | None = Field(default=None, max_length=MAX_GPX_CHARS)
    ride_date: date | None = None
    mode: Literal["chill", "steady", "tempo", "race"] | None = None
    watts: float | None = Field(default=None, gt=0, le=2000)
    watts_kind: Literal["np", "avg"] = "np"
    context: Literal["solo", "group", "race"] = "solo"
    group_size: int | None = Field(default=None, ge=2, le=300)
    temperature_c: float | None = Field(default=None, ge=-30, le=50)


@router.post("/route-estimate")
async def route_estimate(
    body: RouteEstimateBody,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    """Moving time and average speed for a route, at an intensity mode or a power target,
    solo, in a group or in a race. Nothing is written."""
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    stored = AthleteProfileSchema.model_validate(row.profile) if row and row.profile else None
    try:
        return await estimate_route(RouteRequest(**body.model_dump()), client, stored)
    except RouteEstimateError as exc:
        raise HTTPException(422, str(exc)) from None
