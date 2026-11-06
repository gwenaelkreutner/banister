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
from app.bot.text_format import to_telegram_html
from app.config import settings
from app.core.localization import t
from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.providers.intervals.client import IntervalsClient
from app.services.session_review import (
    assemble_review_context,
    backfill_rpe_from_source,
    fetch_dfa,
)

logger = logging.getLogger(__name__)
router = Router(name="review")

_RECENT_LIMIT = 5


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


async def _backfill_rpe_from_source(log: SessionLog) -> None:
    await backfill_rpe_from_source(log, _client())


async def _fetch_dfa(log: SessionLog):
    if not log.source_activity_id:
        return None
    return await fetch_dfa(log, _client())


@router.message(Command("review"))
async def cmd_review(message: Message, session: AsyncSession, user: User) -> None:
    if user is None or not user.onboarding_completed:
        await message.answer(t("review.setup_first"))
        return

    logs = await repo.session_log_repo.get_recent_for_user(
        session, user.id, limit=_RECENT_LIMIT
    )
    if not logs:
        await message.answer(t("review.no_sessions"))
        return

    await message.answer(
        t("review.choose_session"),
        reply_markup=recent_sessions_keyboard(logs),
    )


async def _run_review(
    edit_target, session: AsyncSession, user: User, log: SessionLog
) -> None:
    # Import local (pas en tête de module) : monkeypatchable en test, comme run_chat
    # dans app/bot/routers/chat.py.
    from app.llm.review import generate_session_review

    await _backfill_rpe_from_source(log)
    ctx = await assemble_review_context(session, user, log)
    dfa = await _fetch_dfa(log)
    text = await generate_session_review(ctx, dfa=dfa)
    await edit_target.edit_text(to_telegram_html(text), parse_mode="HTML")


@router.callback_query(F.data.startswith("review:pick:"))
async def cb_review_pick(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, _, log_id_hex = callback.data.split(":")
    log = await repo.session_log_repo.get_by_id(session, uuid.UUID(hex=log_id_hex))
    if log is None or log.user_id != user.id:
        await callback.answer(t("review.session_unavailable"), show_alert=True)
        return

    await callback.answer(t("review.in_progress_callback"))
    await callback.message.edit_text(t("review.preparing"))
    await _run_review(callback.message, session, user, log)
