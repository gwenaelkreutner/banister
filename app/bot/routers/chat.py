"""
Router : chat contextuel agentique.

Intercepte tous les messages texte libres quand l'utilisateur est en PlanStates.ACTIVE.
Appelle le cycle agentique (Tool Use) via app/llm/chat.py.
"""

import logging
import re
from html import escape

# Supprime les blocs de caractères CJK (chinois/japonais/coréen) parasites
_CJK_RE = re.compile(r'[\u3000-\u9fff\uf900-\ufaff\ufe30-\ufe4f]+')


def _strip_cjk(text: str) -> str:
    return _CJK_RE.sub('', text).strip()

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm.attributes import flag_modified

from app.bot.states import PlanStates
from app.db import repositories as repo
from app.db.models.user import User
from app.engine.plan_modifier import apply_proposed_modification, apply_session_adjustment

logger = logging.getLogger(__name__)
router = Router()

_FALLBACK_ERROR = (
    "⚠️ Je rencontre un problème technique en ce moment. "
    "Réessaie dans quelques instants ou utilise /forme pour consulter tes métriques."
)


# ── Handler principal — tout message libre en mode ACTIVE ─────────────────────

@router.message(StateFilter(PlanStates.ACTIVE, None), F.text)
async def handle_chat_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    # Restauration d'état après redémarrage du bot (MemoryStorage perd les états)
    current_state = await state.get_state()
    if current_state is None:
        if not (user and user.onboarding_completed):
            return  # Pas encore onboardé — laisser passer aux autres handlers
        await state.set_state(PlanStates.ACTIVE)

    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        from app.llm.chat import run_chat
        response_text, intent, tool_used, pending_proposal = await run_chat(
            user_message=message.text,
            user=user,
            session=session,
        )
    except Exception:
        logger.exception("Erreur chat agentique")
        await message.answer(_FALLBACK_ERROR)
        return

    # Sauvegarder les deux messages en DB
    await repo.chat_repo.create_message(
        session=session,
        user_id=user.id,
        role="user",
        content=message.text,
        intent=intent,
    )
    await repo.chat_repo.create_message(
        session=session,
        user_id=user.id,
        role="assistant",
        content=response_text,
        intent=intent,
        tool_used=tool_used,
    )

    # Si une proposition de modification a été faite → stocker en FSM + afficher les boutons
    if pending_proposal:
        await state.update_data(pending_modification=pending_proposal)
        await state.set_state(PlanStates.PENDING_MODIFICATION)
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Appliquer", callback_data="chat:apply"),
            InlineKeyboardButton(text="❌ Annuler", callback_data="chat:cancel"),
        ]])
        proposal_text = escape(_strip_cjk(response_text)).strip() or _FALLBACK_ERROR
        await message.answer(proposal_text, reply_markup=kb, parse_mode="HTML")
        return

    text = escape(_strip_cjk(response_text)).strip()
    if not text:
        text = _FALLBACK_ERROR
    await message.answer(text, parse_mode="HTML")


# ── Callbacks confirmation modification ──────────────────────────────────────

@router.callback_query(PlanStates.PENDING_MODIFICATION, F.data == "chat:apply")
async def cb_apply_modification(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    fsm_data = await state.get_data()
    proposal = fsm_data.get("pending_modification")

    if proposal is None:
        await callback.answer("Proposition expirée.", show_alert=True)
        await state.set_state(PlanStates.ACTIVE)
        return

    plan = await repo.plan_repo.get_active_plan(session, user.id)
    if plan is None:
        await callback.answer("Plan introuvable.", show_alert=True)
        await state.set_state(PlanStates.ACTIVE)
        return

    is_session_adj = proposal.get("type") == "session_adjustment"
    if is_session_adj:
        success = apply_session_adjustment(plan, proposal)
    else:
        success = apply_proposed_modification(plan, proposal)

    if success:
        flag_modified(plan, "plan_technical")
        await session.flush()
    await state.update_data(pending_modification=None)
    await state.set_state(PlanStates.ACTIVE)

    if success:
        if is_session_adj:
            summary = proposal.get("summary", "Séance modifiée")
            await callback.message.edit_text(
                f"✅ <b>Séance mise à jour !</b>\n\n"
                f"{escape(summary)}\n"
                f"Utilise /plan pour voir le planning.",
                parse_mode="HTML",
            )
        else:
            week_num = proposal.get("week_number", "?")
            tss_after = proposal.get("tss_after", "?")
            await callback.message.edit_text(
                f"✅ <b>Plan mis à jour !</b>\n\n"
                f"Semaine {week_num} ajustée — TSS cible : {tss_after}\n"
                f"Utilise /plan pour voir les séances modifiées.",
                parse_mode="HTML",
            )
    else:
        await callback.message.edit_text("❌ Impossible d'appliquer la modification.")

    await callback.answer()


@router.callback_query(PlanStates.PENDING_MODIFICATION, F.data == "chat:cancel")
async def cb_cancel_modification(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.update_data(pending_modification=None)
    await state.set_state(PlanStates.ACTIVE)
    await callback.message.edit_text("❌ Modification annulée. Le plan reste inchangé.")
    await callback.answer()
