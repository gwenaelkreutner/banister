"""Internal read-only API for other services on the same host (Hermes mobility coach).

Never routed publicly: Banister's port is bound to 127.0.0.1 and other containers reach it
over the `banister_default` Docker network. A shared bearer token is still required —
the same port serves the Telegram webhook, so the endpoint must not rely on network
placement alone. No token configured ⇒ the whole router answers 404 (fail closed).
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repositories as repo
from app.db.client import get_session
from app.services.sport_context import MAX_AHEAD, MAX_DAYS, build_sport_context


def require_token(authorization: str = Header(default="")) -> None:
    expected = settings.internal_api_token.get_secret_value()
    if not expected:
        raise HTTPException(404)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, expected):
        raise HTTPException(401)


router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_token)])


@router.get("/context")
async def sport_context(
    days: int = Query(7, ge=1, le=MAX_DAYS),
    ahead: int = Query(7, ge=1, le=MAX_AHEAD),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await repo.user_repo.get_single_user(session)
    if user is None:
        raise HTTPException(404, "no athlete configured")
    return await build_sport_context(session, user.id, days=days, ahead=ahead)
