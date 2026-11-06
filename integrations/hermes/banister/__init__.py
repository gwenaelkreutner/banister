"""Hermes plugin for Banister: an adapter over its agent API (docs/AGENT_INTEGRATION.md).

The agent understands and chooses; Banister computes and checks. `tools.py` is what the model can
do; `messaging.py` is what only code does: delivering messages and carrying the athlete's taps.
"""
from __future__ import annotations

import logging

from . import messaging, tools
from .client import BanisterError, call

logger = logging.getLogger("hermes_plugins.banister")


def _observe_reply(assistant_response: str = "", **_kw) -> None:
    """Let Banister record any figure that looks wrong. The reply itself is never touched, and
    nothing here can hold it back or break it."""
    if not assistant_response or not any(c.isdigit() for c in assistant_response):
        return
    try:
        call(
            "POST", "/verify-reply",
            {"text": assistant_response, "retrieved": tools.retrieved_values()},
            timeout=5,
        )
    except BanisterError as exc:
        logger.debug("banister: reply not observed (%s)", exc)


def register(ctx) -> None:
    messaging._state["ctx"] = ctx
    tools.send_card = messaging.send_card
    for schema, handler in tools.TOOLS:
        ctx.register_tool(name=schema["name"], toolset="banister", schema=schema, handler=handler)
    ctx.register_platform_handler("telegram", messaging.on_telegram)
    ctx.register_hook("post_llm_call", _observe_reply)
