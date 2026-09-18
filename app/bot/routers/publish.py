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
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
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


@router.message(Command("unpublish"))
async def cmd_unpublish(message: Message, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer("Complète d'abord ton onboarding avec /start.")
        return
    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        # spec 010 US3 — mode libre : proposer de retirer les publications mode libre
        # au lieu de "Aucun plan actif", symétrique de la même bascule que /goal a déjà.
        from app.db.repositories import freestyle_publication_repo

        active_freestyle = await freestyle_publication_repo.get_active_for_user(
            session, user.id
        )
        if not active_freestyle:
            await message.answer("Rien n'est actuellement publié dans ton calendrier.")
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🗑 Tout retirer ({len(active_freestyle)})",
                        callback_data="pub:withdrawall_freestyle",
                    ),
                    InlineKeyboardButton(text="Annuler", callback_data="pub:withdrawcancel"),
                ]
            ]
        )
        await message.answer(
            f"Retirer les <b>{len(active_freestyle)}</b> séance(s) mode libre que j'ai "
            "publiées dans ton calendrier intervals.icu ? Tes propres entrées et celles "
            "d'autres outils ne sont pas touchées.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return
    active = await publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    if not active:
        await message.answer("Rien n'est actuellement publié dans ton calendrier.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🗑 Tout retirer ({len(active)})", callback_data="pub:withdrawall"
                ),
                InlineKeyboardButton(text="Annuler", callback_data="pub:withdrawcancel"),
            ]
        ]
    )
    await message.answer(
        f"Retirer les <b>{len(active)}</b> séances que j'ai publiées dans ton calendrier "
        "intervals.icu ? Tes propres entrées et celles d'autres outils ne sont pas touchées.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "pub:withdrawcancel")
async def cb_withdraw_cancel(callback: CallbackQuery):
    await callback.answer("Annulé.")
    await callback.message.edit_text("Rien retiré.", parse_mode="HTML")


@router.callback_query(F.data == "pub:withdrawall")
async def cb_withdraw_all(callback: CallbackQuery, session: AsyncSession, user: User):
    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await callback.answer("Aucun plan actif.", show_alert=True)
        return
    await callback.answer("Retrait en cours…")
    await callback.message.edit_text("⏳ Retrait des séances…", parse_mode="HTML")
    withdrawn, failed = await publication.withdraw_all_publications(
        session, _client(), user, plan
    )
    if failed:
        await callback.message.edit_text(
            f"⚠️ {withdrawn} retirées, {failed} échec(s). Relance /unpublish pour réessayer.",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"✅ {withdrawn} séance(s) retirée(s). Ton calendrier ne contient "
            "plus rien de ma part.",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "pub:withdrawall_freestyle")
async def cb_withdraw_all_freestyle(callback: CallbackQuery, session: AsyncSession, user: User):
    """spec 010 US3 — retire uniquement les publications mode libre, jamais les entrées
    d'un plan (une table à part, pas un filtre qui pourrait se tromper — research.md
    Decision 4)."""
    await callback.answer("Retrait en cours…")
    await callback.message.edit_text("⏳ Retrait des séances…", parse_mode="HTML")
    withdrawn, failed = await publication.withdraw_freestyle_publications(
        session, _client(), user
    )
    if failed:
        await callback.message.edit_text(
            f"⚠️ {withdrawn} retirées, {failed} échec(s). Relance /unpublish pour réessayer.",
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"✅ {withdrawn} séance(s) retirée(s). Ton calendrier ne contient "
            "plus rien de ma part.",
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
    except publication.StaleApprovalError:
        await callback.message.edit_text(
            "⚠️ Ton plan a changé depuis cette demande — rien n'a été publié. "
            "Relance /publish pour approuver la version à jour.",
            parse_mode="HTML",
        )
        return
    except publication.PublicationNotAuthorized:
        logger.warning("Publication refused for approval %s", approval_id)
        await callback.message.edit_text(
            "⚠️ Cette demande n'est plus valide — relance /publish.", parse_mode="HTML"
        )
        return
    except Exception:  # noqa: BLE001
        logger.exception("Publication failed for approval %s", approval_id)
        await callback.message.edit_text(
            "⚠️ La publication a échoué en cours de route. Les séances déjà écrites "
            "sont enregistrées — relance /publish pour reprendre.",
            parse_mode="HTML",
        )
        return

    await callback.message.edit_text(report.text, parse_mode="HTML")
