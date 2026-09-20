"""`/review` — relire une séance déjà loggée.

Une étape, en clavier inline, pas de FSM (voir app/bot/keyboards/review.py pour la
justification — même précédent que `freestyle:publish:<id>`, spec 010) : choix de la
séance parmi les 5 dernières loggées (get_recent_for_user), puis synthèse immédiate.
Un ancien picker de profondeur (brief/default/deep, + raccourci CLI `/review brief`) a
été retiré (2026-09-21, décision owner) — voir app/llm/prompts.py.

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

from app.bot.keyboards.review import recent_sessions_keyboard
from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.services.session_review import assemble_review_context

logger = logging.getLogger(__name__)
router = Router(name="review")

_RECENT_LIMIT = 5


@router.message(Command("review"))
async def cmd_review(message: Message, session: AsyncSession, user: User) -> None:
    if user is None or not user.onboarding_completed:
        await message.answer("Fais d'abord /setup.")
        return

    logs = await repo.session_log_repo.get_recent_for_user(
        session, user.id, limit=_RECENT_LIMIT
    )
    if not logs:
        await message.answer("Aucune séance loggée pour l'instant.")
        return

    await message.answer(
        "Quelle séance veux-tu relire ?",
        reply_markup=recent_sessions_keyboard(logs),
    )


async def _run_review(
    edit_target, session: AsyncSession, user: User, log: SessionLog
) -> None:
    # Import local (pas en tête de module) : monkeypatchable en test, comme run_chat
    # dans app/bot/routers/chat.py.
    from app.llm.review import generate_session_review

    ctx = await assemble_review_context(session, user, log)
    text = await generate_session_review(ctx)
    await edit_target.edit_text(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("review:pick:"))
async def cb_review_pick(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, log_id_hex = callback.data.split(":")
    log = await repo.session_log_repo.get_by_id(session, uuid.UUID(hex=log_id_hex))
    if log is None or log.user_id != user.id:
        await callback.answer("Cette séance n'est plus disponible.", show_alert=True)
        return

    await callback.answer("Synthèse en cours…")
    await callback.message.edit_text("⏳ Je prépare la synthèse…")
    await _run_review(callback.message, session, user, log)
