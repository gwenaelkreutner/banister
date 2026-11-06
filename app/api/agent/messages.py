"""What the integration's own code handles, never the model.

`/messages` is what Banister has to say; `/actions` carries the athlete's taps on the buttons of
those messages and of the cards. Actions write to the calendar and the plan: they MUST NOT be
offered to the model as tools. `/verify-reply` checks the model's final text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent.deps import current_user, intervals_client
from app.db.client import get_session
from app.db.models.user import User
from app.services import agent_actions, outbox
from app.services.agent_verification import verify_agent_reply

router = APIRouter()


@router.get("/messages/pending")
async def pending(
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"messages": await outbox.pending_messages(session, limit=limit)}


@router.post("/messages/{message_id}/delivered")
async def delivered(message_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    return {"updated": await outbox.mark_delivered(session, message_id)}


class Action(BaseModel):
    action: str = Field(min_length=1, max_length=64)


@router.post("/actions")
async def action(
    body: Action,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """`{"text", "agent_prompt"}`: the new text of the card, and what the agent should do next."""
    try:
        return await agent_actions.dispatch(session, user, body.action, intervals_client)
    except agent_actions.ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


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
