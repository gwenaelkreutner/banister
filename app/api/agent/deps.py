"""What every agent route needs: the token check, the athlete, the data source client."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repositories as repo
from app.db.client import get_session
from app.db.models.user import User
from app.providers.intervals.client import IntervalsClient


def require_agent_token(authorization: str = Header(default="")) -> None:
    expected = settings.agent_api_token.get_secret_value()
    if not expected:
        raise HTTPException(404)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(401)


async def current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = await repo.user_repo.get_single_user(session)
    if user is None:
        raise HTTPException(404, "no athlete configured")
    return user


def intervals_client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(), athlete_id=settings.intervals_athlete_id
    )
