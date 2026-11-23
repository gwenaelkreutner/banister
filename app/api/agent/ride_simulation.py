"""Simulate a ride before doing it (allure-cycling physics + Banister's form projection)."""

from __future__ import annotations

import datetime as dt
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
from app.services.ride_simulation import RideRequest, RideSimulationError, simulate_ride

router = APIRouter()

MAX_GPX_CHARS = 8_000_000


class RideSimulationBody(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    gpx: str | None = Field(default=None, max_length=MAX_GPX_CHARS)
    track_ride_date: dt.date | None = None
    date: dt.date | None = None
    mode: Literal["chill", "steady", "tempo", "race"] | None = None
    watts: float | None = Field(default=None, gt=0, le=2000)
    watts_kind: Literal["np", "avg"] = "np"
    context: Literal["solo", "group", "race"] = "solo"
    group_size: int | None = Field(default=None, ge=2, le=300)
    temperature_c: float | None = Field(default=None, ge=-30, le=50)


@router.post("/simulate-ride")
async def simulate(
    body: RideSimulationBody,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    client: IntervalsClient = Depends(intervals_client),
) -> dict:
    """Time, route description (climbs), effort (TSS, kJ) and, with a date, the form after
    the ride. Nothing is written."""
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    stored = AthleteProfileSchema.model_validate(row.profile) if row and row.profile else None
    try:
        request = RideRequest(**body.model_dump())
        return await simulate_ride(request, client, session, user.id, stored)
    except RideSimulationError as exc:
        raise HTTPException(422, str(exc)) from None
