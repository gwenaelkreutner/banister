"""Everything between Banister and the chat that does not go through the model.

Delivers what Banister has to say, in order, and forwards the athlete's taps. Wording and buttons
come from Banister: an action is an opaque string, and this file knows nothing about its meaning.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .client import BanisterError, call, owner_chat_id

logger = logging.getLogger("hermes_plugins.banister")

CALLBACK_PREFIX = "ban:"
POLL_SECONDS = 20
_state: dict = {"bot": None, "loop": None, "task": None, "ctx": None}


def authorized(user_id) -> bool:
    owner = owner_chat_id()
    return bool(owner) and str(user_id) == owner  # fail closed


def _keyboard(rows):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if not rows:
        return None
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(b["label"], callback_data=f"{CALLBACK_PREFIX}{b['action']}")
            for b in row
        ]
        for row in rows
    ])


async def _send(message: dict) -> bool:
    bot, chat_id = _state["bot"], owner_chat_id()
    if not (bot and chat_id):
        return False
    await bot.send_message(
        chat_id,
        message["text"],
        parse_mode=message.get("parse_mode"),
        reply_markup=_keyboard(message.get("buttons")),
        disable_notification=bool(message.get("silent")),
    )
    return True


def send_card(card: dict) -> bool:
    """Called from a tool, on the agent's thread. Without a gateway (CLI, Kanban worker) no card
    can be shown, and the tool tells the agent so."""
    loop = _state["loop"]
    if not (loop and loop.is_running()):
        return False
    try:
        if asyncio.get_running_loop() is loop:
            loop.create_task(_send(card))
            return True
    except RuntimeError:
        pass
    try:
        return asyncio.run_coroutine_threadsafe(_send(card), loop).result(timeout=20)
    except Exception:
        logger.exception("banister: card not sent")
        return False


def _session_key(chat_id: str) -> str:
    """<home>/plugins/banister/: a profile's home sits under profiles/, the default one is `main`."""
    home = Path(__file__).resolve().parents[2]
    namespace = home.name if home.parent.name == "profiles" else "main"
    return f"agent:{namespace}:telegram:dm:{chat_id}"


def _ask_agent(prompt: str | None) -> bool:
    """Start a turn of the agent in the athlete's chat, so it continues in its own words. Hermes
    needs a session holding a message from the athlete: False when it refuses outright."""
    ctx, chat_id = _state["ctx"], owner_chat_id()
    if not (prompt and ctx and chat_id):
        return False
    # Join the existing chat: Hermes does not route a turn to a hand-built `origin` here.
    return bool(ctx.inject_message(prompt, session_key=_session_key(chat_id)))


async def _hand_over(loop, message: dict) -> None:
    """Never lose a follow-up silently: if the agent cannot be started, say how to ask for it."""
    prompt = message.get("agent_prompt")
    if not prompt or await loop.run_in_executor(None, lambda: _ask_agent(prompt)):
        return
    logger.warning("banister: the agent could not be asked to follow up")
    if message.get("handover_hint"):
        await _send({"text": message["handover_hint"]})


async def _deliver_pending(loop) -> None:
    pending = await loop.run_in_executor(None, lambda: call("GET", "/messages/pending"))
    for message in pending["messages"]:
        if not await _send(message):
            return
        await loop.run_in_executor(
            None, lambda m=message: call("POST", f"/messages/{m['id']}/delivered")
        )
        await _hand_over(loop, message)


async def _outbox_loop() -> None:
    await asyncio.sleep(10)
    loop = asyncio.get_running_loop()
    while True:
        try:
            await _deliver_pending(loop)
        except asyncio.CancelledError:
            raise
        except BanisterError as exc:
            logger.debug("banister: outbox unavailable (%s)", exc)
        except Exception:
            logger.exception("banister: outbox")
        await asyncio.sleep(POLL_SECONDS)


async def on_button(update, context) -> None:
    query = update.callback_query
    if not authorized(getattr(query.from_user, "id", "")):
        await query.answer("Not allowed.", show_alert=True)
        return
    action = query.data[len(CALLBACK_PREFIX):]
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: call("POST", "/actions", {"action": action}, timeout=90)
        )
    except BanisterError as exc:
        # A refused or failed action leaves the card and its buttons in place.
        await query.answer(str(exc.detail)[:190], show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(result["text"], parse_mode=result.get("parse_mode"))
    await _hand_over(loop, result)


def on_telegram(application, adapter) -> None:
    from telegram.ext import CallbackQueryHandler

    application.add_handler(CallbackQueryHandler(on_button, pattern=rf"^{CALLBACK_PREFIX}"))
    _state["bot"] = application.bot
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("banister: no asyncio loop when connecting to Telegram")
        return
    _state["loop"] = loop
    if _state["task"] is None or _state["task"].done():
        _state["task"] = loop.create_task(_outbox_loop())
    logger.info("banister: connected to Telegram")
