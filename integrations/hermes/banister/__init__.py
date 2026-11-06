"""Hermes plugin for Banister: an adapter over its agent API (docs/AGENT_INTEGRATION.md).

The agent understands and chooses; Banister computes and checks. `tools.py` is what the model can
do; `messaging.py` is what only code does: delivering messages and carrying the athlete's taps.
"""
from __future__ import annotations

import logging

from . import messaging, tools
from .client import BanisterError, call

logger = logging.getLogger("hermes_plugins.banister")


def _check_reply(response_text: str, **_kw) -> str | None:
    """Withhold sentences stating a metric value the agent did not retrieve. If Banister cannot
    be reached the reply goes out unchanged, as in the built-in chat."""
    if not response_text or not any(c.isdigit() for c in response_text):
        return None
    try:
        result = call(
            "POST", "/verify-reply",
            {"text": response_text, "retrieved": tools.retrieved_values()},
            timeout=8,
        )
    except BanisterError as exc:
        logger.warning("banister: reply not checked (%s)", exc)
        return None
    return None if result.get("ok") else result.get("text")


def register(ctx) -> None:
    messaging._state["ctx"] = ctx
    tools.send_card = messaging.send_card
    for schema, handler in tools.TOOLS:
        ctx.register_tool(name=schema["name"], toolset="banister", schema=schema, handler=handler)
    ctx.register_platform_handler("telegram", messaging.on_telegram)
    ctx.register_hook("transform_llm_output", _check_reply)
