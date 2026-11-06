"""Agent API: lets an external agent (Hermes plugin, MCP server, your own) coach with Banister.

The agent understands the athlete and chooses or composes; Banister computes, checks and, once
the athlete approves, acts. Contract: docs/AGENT_INTEGRATION.md.

Bound to the same local port as the rest of the app and never routed publicly. It has its own
bearer token, separate from `INTERNAL_API_TOKEN` because it can write. No token configured
means the whole router answers 404 (fail closed), so a default install exposes nothing.
"""

from fastapi import APIRouter, Depends

from app.api.agent import athlete, messages, plan, sessions
from app.api.agent.deps import intervals_client, require_agent_token

router = APIRouter(prefix="/agent/v1", dependencies=[Depends(require_agent_token)])
router.include_router(athlete.router)
router.include_router(sessions.router)
router.include_router(plan.router)
router.include_router(messages.router)

__all__ = ["intervals_client", "router"]
