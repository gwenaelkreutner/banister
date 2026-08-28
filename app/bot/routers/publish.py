"""`/publish` — approve a horizon of sessions and write them to the athlete's
intervals.icu calendar (spec 005 US1 = T017-T019).

Registered before `chat_router` in app/bot/setup.py (catch-all ordering). This feature is
the project's first outbound mutation: nothing is written without a recorded, pending
PublicationApproval that the athlete explicitly approves here. The content-hash staleness
gate (FR-004) lands with US2 (T022).
"""
from __future__ import annotations

import logging
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.publish import approval_keyboard
from app.config import settings
from app.db.models.user import User
from app.db.repositories import plan_repo, publication_repo
from app.providers.intervals.client import IntervalsClient
from app.services import publication

logger = logging.getLogger(__name__)
router = Router(name="publish")


def _client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )


@router.message(Command("publish"))
async def cmd_publish(message: Message, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer("Complète d'abord ton onboarding avec /start.")
        return

    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await message.answer("Aucun plan actif — lance /setup d'abord.")
        return

    request = await publication.request_publication(session, user, plan)
    if request.session_count == 0:
        await message.answer(
            "Aucune séance structurée à publier sur la période "
            f"({request.approval.horizon_start:%d/%m} → {request.approval.horizon_end:%d/%m})."
        )
        return

    await message.answer(
        request.text,
        reply_markup=approval_keyboard(request.approval.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pub:decline:"))
async def cb_decline(callback: CallbackQuery, session: AsyncSession, user: User):
    approval_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    approval = await publication_repo.get_approval(session, approval_id)
    if approval is None or approval.user_id != user.id:
        await callback.answer("Demande introuvable.", show_alert=True)
        return
    if approval.status == "pending":
        await publication_repo.mark_declined(session, approval_id)

    await callback.answer("Annulé — rien n'a été publié.")
    await callback.message.edit_text(
        "❌ Publication annulée — rien n'a été écrit dans ton calendrier.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pub:approve:"))
async def cb_approve(callback: CallbackQuery, session: AsyncSession, user: User):
    approval_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    approval = await publication_repo.get_approval(session, approval_id)
    if approval is None or approval.user_id != user.id:
        await callback.answer("Demande introuvable.", show_alert=True)
        return
    if approval.status != "pending":
        await callback.answer("Cette demande a déjà été traitée.", show_alert=True)
        return

    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None or plan.id != approval.plan_id:
        await callback.answer("Le plan a changé — relance /publish.", show_alert=True)
        return

    await callback.answer("Publication en cours…")
    await callback.message.edit_text("⏳ Publication vers intervals.icu…", parse_mode="HTML")

    await publication_repo.mark_approved(session, approval_id)
    try:
        report = await publication.execute_publication(
            session, _client(), user, plan, approval
        )
    except Exception:  # noqa: BLE001
        logger.exception("Publication failed for approval %s", approval_id)
        await callback.message.edit_text(
            "⚠️ La publication a échoué en cours de route. Les séances déjà écrites "
            "sont enregistrées — relance /publish pour reprendre.",
            parse_mode="HTML",
        )
        return

    await callback.message.edit_text(report.text, parse_mode="HTML")
