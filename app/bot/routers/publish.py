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
from app.core.localization import t
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
        await message.answer(t("publish.onboarding_required"))
        return

    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await message.answer(t("publish.no_active_plan_setup"))
        return

    request = await publication.request_publication(session, user, plan)
    if request.session_count == 0:
        await message.answer(
            t(
                "publish.no_structured_sessions",
                start=request.approval.horizon_start.strftime("%d/%m"),
                end=request.approval.horizon_end.strftime("%d/%m"),
            )
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
        await message.answer(t("publish.onboarding_required"))
        return
    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        # spec 010 US3 — mode libre : proposer de retirer les publications mode libre
        # au lieu de "Aucun plan actif", symétrique de la même bascule que /goal a déjà.
        from app.db.repositories import freestyle_publication_repo

        active_freestyle = await freestyle_publication_repo.get_active_for_user(session, user.id)
        if not active_freestyle:
            await message.answer(t("publish.nothing_published"))
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("publish.withdraw_all_button", count=len(active_freestyle)),
                        callback_data="pub:withdrawall_freestyle",
                    ),
                    InlineKeyboardButton(
                        text=t("publish.cancel_plain_button"), callback_data="pub:withdrawcancel"
                    ),
                ]
            ]
        )
        await message.answer(
            t("publish.withdraw_freestyle_prompt", count=len(active_freestyle)),
            reply_markup=kb,
            parse_mode="HTML",
        )
        return
    active = await publication_repo.get_active_entries_for_plan(session, user.id, plan.id)
    if not active:
        await message.answer(t("publish.nothing_published"))
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("publish.withdraw_all_button", count=len(active)),
                    callback_data="pub:withdrawall",
                ),
                InlineKeyboardButton(
                    text=t("publish.cancel_plain_button"), callback_data="pub:withdrawcancel"
                ),
            ]
        ]
    )
    await message.answer(
        t("publish.withdraw_plan_prompt", count=len(active)),
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "pub:withdrawcancel")
async def cb_withdraw_cancel(callback: CallbackQuery):
    await callback.answer(t("publish.cancelled_short"))
    await callback.message.edit_text(t("publish.nothing_withdrawn"), parse_mode="HTML")


@router.callback_query(F.data == "pub:withdrawall")
async def cb_withdraw_all(callback: CallbackQuery, session: AsyncSession, user: User):
    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await callback.answer(t("publish.no_active_plan"), show_alert=True)
        return
    await callback.answer(t("publish.withdraw_in_progress"))
    await callback.message.edit_text(t("publish.withdrawing_sessions"), parse_mode="HTML")
    withdrawn, failed = await publication.withdraw_all_publications(session, _client(), user, plan)
    if failed:
        await callback.message.edit_text(
            t("publish.withdraw_partial_failure", withdrawn=withdrawn, failed=failed),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            t("publish.withdraw_success", withdrawn=withdrawn),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "pub:withdrawall_freestyle")
async def cb_withdraw_all_freestyle(callback: CallbackQuery, session: AsyncSession, user: User):
    """spec 010 US3 — retire uniquement les publications mode libre, jamais les entrées
    d'un plan (une table à part, pas un filtre qui pourrait se tromper — research.md
    Decision 4)."""
    await callback.answer(t("publish.withdraw_in_progress"))
    await callback.message.edit_text(t("publish.withdrawing_sessions"), parse_mode="HTML")
    withdrawn, failed = await publication.withdraw_freestyle_publications(session, _client(), user)
    if failed:
        await callback.message.edit_text(
            t("publish.withdraw_partial_failure", withdrawn=withdrawn, failed=failed),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            t("publish.withdraw_success", withdrawn=withdrawn),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("pub:decline:"))
async def cb_decline(callback: CallbackQuery, session: AsyncSession, user: User):
    approval_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    approval = await publication_repo.get_approval(session, approval_id)
    if approval is None or approval.user_id != user.id:
        await callback.answer(t("publish.request_not_found"), show_alert=True)
        return
    if approval.status == "pending":
        await publication_repo.mark_declined(session, approval_id)

    await callback.answer(t("publish.publication_cancelled_short"))
    await callback.message.edit_text(
        t("publish.publication_cancelled"),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pub:approve:"))
async def cb_approve(callback: CallbackQuery, session: AsyncSession, user: User):
    approval_id = uuid.UUID(callback.data.rsplit(":", 1)[1])
    approval = await publication_repo.get_approval(session, approval_id)
    if approval is None or approval.user_id != user.id:
        await callback.answer(t("publish.request_not_found"), show_alert=True)
        return
    if approval.status != "pending":
        await callback.answer(t("publish.request_already_handled"), show_alert=True)
        return

    plan = await plan_repo.get_active_plan(session, user.id)
    if plan is None or plan.id != approval.plan_id:
        await callback.answer(t("publish.plan_changed_retry"), show_alert=True)
        return

    await callback.answer(t("publish.publication_in_progress"))
    await callback.message.edit_text(t("publish.publishing_sessions"), parse_mode="HTML")

    await publication_repo.mark_approved(session, approval_id)
    try:
        report = await publication.execute_publication(session, _client(), user, plan, approval)
    except publication.StaleApprovalError:
        await callback.message.edit_text(
            t("publish.stale_approval"),
            parse_mode="HTML",
        )
        return
    except publication.PublicationNotAuthorized:
        logger.warning("Publication refused for approval %s", approval_id)
        await callback.message.edit_text(t("publish.invalid_request"), parse_mode="HTML")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Publication failed for approval %s", approval_id)
        await callback.message.edit_text(
            t("publish.publication_failed"),
            parse_mode="HTML",
        )
        return

    await callback.message.edit_text(report.text, parse_mode="HTML")
