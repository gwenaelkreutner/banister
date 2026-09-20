"""`/review` — relire une séance déjà loggée, à la profondeur choisie.

Deux étapes, toutes deux en clavier inline, pas de FSM (voir app/bot/keyboards/review.py
pour la justification — même précédent que `freestyle:publish:<id>`, spec 010) :
  1. Choix de la séance parmi les 5 dernières loggées (get_recent_for_user).
  2. Choix de la profondeur (brief/default/deep) — sauté si donnée en argument CLI
     (`/review brief` ou `/review deep`), qui reste un raccourci, pas le chemin principal.

Enregistré après session_log_router dans app/bot/setup.py (proximité logique — même
domaine que la capture RPE), avant chat_router (catch-all, doit rester dernier).
"""
from __future__ import annotations

import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.review import depth_keyboard, recent_sessions_keyboard
from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.services.session_review import assemble_review_context

logger = logging.getLogger(__name__)
router = Router(name="review")

_VALID_CLI_DEPTHS = {"brief", "deep"}
_RECENT_LIMIT = 5


def _parse_cli_depth(message: Message) -> str | None:
    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        return None
    candidate = parts[1].lower()
    return candidate if candidate in _VALID_CLI_DEPTHS else None


@router.message(Command("review"))
async def cmd_review(message: Message, session: AsyncSession, user: User) -> None:
    if user is None or not user.onboarding_completed:
        await message.answer("Fais d'abord /setup.")
        return

    cli_depth = _parse_cli_depth(message)
    logs = await repo.session_log_repo.get_recent_for_user(
        session, user.id, limit=_RECENT_LIMIT
    )
    if not logs:
        await message.answer("Aucune séance loggée pour l'instant.")
        return

    await message.answer(
        "Quelle séance veux-tu relire ?",
        reply_markup=recent_sessions_keyboard(logs, cli_depth),
    )


async def _run_review(
    edit_target, session: AsyncSession, user: User, log: SessionLog, depth: str
) -> None:
    # Import local (pas en tête de module) : monkeypatchable en test, comme run_chat
    # dans app/bot/routers/chat.py.
    from app.llm.review import generate_session_review

    ctx = await assemble_review_context(session, user, log)
    text = await generate_session_review(ctx, depth)
    await edit_target.edit_text(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("review:pick:"))
async def cb_review_pick(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, log_id_hex, depth_token = callback.data.split(":")
    log = await repo.session_log_repo.get_by_id(session, uuid.UUID(hex=log_id_hex))
    if log is None or log.user_id != user.id:
        await callback.answer("Cette séance n'est plus disponible.", show_alert=True)
        return

    if depth_token in _VALID_CLI_DEPTHS:
        await callback.answer("Synthèse en cours…")
        await callback.message.edit_text("⏳ Je prépare la synthèse…")
        await _run_review(callback.message, session, user, log, depth_token)
        return

    await callback.answer()
    await callback.message.edit_text("Quelle profondeur ?", reply_markup=depth_keyboard(log.id))


@router.callback_query(F.data.startswith("review:depth:"))
async def cb_review_depth(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, log_id_hex, depth = callback.data.split(":")
    log = await repo.session_log_repo.get_by_id(session, uuid.UUID(hex=log_id_hex))
    if log is None or log.user_id != user.id:
        await callback.answer("Cette séance n'est plus disponible.", show_alert=True)
        return

    await callback.answer("Synthèse en cours…")
    await callback.message.edit_text("⏳ Je prépare la synthèse…")
    await _run_review(callback.message, session, user, log, depth)
