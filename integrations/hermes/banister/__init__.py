"""Hermes plugin for Banister: an adapter over its agent API (docs/AGENT_INTEGRATION.md).

The agent understands and chooses; Banister computes and checks. The plugin adds what a model
must not do itself: approval cards and their buttons, and a check of the final reply's numbers.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import tools
from .client import BanisterError, call, owner_chat_id

logger = logging.getLogger("hermes_plugins.banister")
CALLBACK_PREFIX = "ban:"
_state: dict = {"bot": None, "loop": None}


def _authorized(user_id) -> bool:
    owner = owner_chat_id()
    return bool(owner) and str(user_id) == owner  # fail closed


async def _send(text: str, buttons: list[tuple[str, str]]) -> bool:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    bot, chat_id = _state["bot"], owner_chat_id()
    if not (bot and chat_id):
        return False
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"{CALLBACK_PREFIX}{data}") for label, data in buttons]]
    )
    await bot.send_message(chat_id, text, reply_markup=keyboard)
    return True


def send_card(text: str, buttons: list[tuple[str, str]]) -> bool:
    """Called from a tool, on the agent's thread. Without a gateway (CLI, Kanban worker) no
    card can be shown, and the tool tells the agent so."""
    loop = _state["loop"]
    if not (loop and loop.is_running()):
        return False
    try:
        if asyncio.get_running_loop() is loop:
            loop.create_task(_send(text, buttons))
            return True
    except RuntimeError:
        pass
    try:
        return asyncio.run_coroutine_threadsafe(_send(text, buttons), loop).result(timeout=20)
    except Exception:
        logger.exception("banister: card not sent")
        return False


async def _on_button(update, context) -> None:
    query = update.callback_query
    if not _authorized(getattr(query.from_user, "id", "")):
        await query.answer("Not allowed.", show_alert=True)
        return
    try:
        _, action, value = query.data.split(":", 2)
    except ValueError:
        await query.answer("Invalid button.")
        return
    loop = asyncio.get_running_loop()
    original = query.message.text if query.message else ""
    routes = {
        "ok": ("POST", f"/proposals/{value}/approve"),
        "no": ("POST", f"/proposals/{value}/decline"),
        "wd": ("DELETE", f"/publications/{value}"),
    }
    try:
        if action == "keep":
            card = await loop.run_in_executor(
                None, lambda: call("GET", f"/publications/{value}/withdrawal-card")
            )
            await query.answer()
            await query.edit_message_text(card["kept_text"])
            return
        if action not in routes:
            await query.answer("Invalid button.")
            return
        method, path = routes[action]
        result = await loop.run_in_executor(None, lambda: call(method, path))
        await query.answer()
        await query.edit_message_text(f"{original}\n\n{result['text']}")
    except BanisterError as exc:
        # A failed write leaves the card and its buttons in place, so a tap can retry.
        await query.answer(str(exc.detail)[:190], show_alert=True)


def _on_telegram(application, adapter) -> None:
    from telegram.ext import CallbackQueryHandler

    application.add_handler(CallbackQueryHandler(_on_button, pattern=r"^ban:"))
    _state["bot"] = application.bot
    try:
        _state["loop"] = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("banister: no asyncio loop when connecting to Telegram")
        return
    logger.info("banister: connected to Telegram")


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
    tools.send_card = send_card
    for schema, handler in tools.TOOLS:
        ctx.register_tool(name=schema["name"], toolset="banister", schema=schema, handler=handler)
    ctx.register_platform_handler("telegram", _on_telegram)
    ctx.register_hook("transform_llm_output", _check_reply)
    ctx.register_skill(
        "coach", Path(__file__).parent / "skills" / "coach" / "SKILL.md",
        description="Cycling coaching with Banister: reading form, finding and composing sessions, proposing them for approval",
    )
