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
from app.config import settings
from app.db import repositories as repo
from app.db.models.session_log import SessionLog
from app.db.models.user import User
from app.providers.intervals.client import IntervalsClient
from app.services.session_review import assemble_review_context

logger = logging.getLogger(__name__)
router = Router(name="review")

_RECENT_LIMIT = 5
_DFA_STREAM_TYPES = ["dfa_a1", "artifacts", "heartrate", "watts"]


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


async def _backfill_rpe_from_source(log: SessionLog) -> None:
    """Rattrapage pour les séances déjà loguées avant que `icu_rpe` soit mappé à
    l'ingestion (2026-09-21) — ou pour un ressenti ajouté sur intervals.icu après coup.
    Le clavier Telegram (`cb_rpe`) reste prioritaire : ne touche `log.rpe` que s'il est
    encore vide (décision owner). Consommé tel quel (échelle standard 1-10, Principe IV,
    app/engine/rpe.py) — plus de bucketing depuis que le stockage est numérique. Mute
    `log` en place — même transaction que le reste du handler, committée par le
    middleware (`session.begin()`), donc persisté."""
    if log.rpe is not None or not log.source_activity_id:
        return
    try:
        activity = await _client().get_activity(log.source_activity_id)
        rpe = activity.get("icu_rpe")
        if rpe is not None:
            log.rpe = rpe
    except Exception:
        logger.warning("Impossible de rattraper le RPE depuis intervals.icu pour /review")


async def _fetch_dfa(log: SessionLog):
    """Best-effort : un appel réseau intervals.icu en plus, séparé du reste de /review
    (DB uniquement) — jamais bloquant. `None` si pas d'activité source (log manuel), si
    l'appel échoue, ou si l'athlète n'a pas d'AlphaHRV (dfa_a1 absent des streams — état
    normal pour la plupart des comptes tant que le champ Garmin n'est pas installé)."""
    if not log.source_activity_id:
        return None
    try:
        from app.engine.dfa import compute_dfa_block
        from app.providers.intervals.streams import streams_to_dict

        raw_streams = await _client().get_activity_streams(
            log.source_activity_id, types=_DFA_STREAM_TYPES
        )
        return compute_dfa_block(streams_to_dict(raw_streams))
    except Exception:
        logger.warning("Impossible de récupérer les streams DFA pour /review")
        return None


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

    await _backfill_rpe_from_source(log)
    ctx = await assemble_review_context(session, user, log)
    dfa = await _fetch_dfa(log)
    text = await generate_session_review(ctx, dfa=dfa)
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
