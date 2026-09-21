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

# Plafond du contexte de reply injecté au LLM — Telegram va jusqu'à 4096 caractères par
# message, mais on ne veut que de quoi identifier de quoi l'athlète parle, pas rejouer
# l'intégralité d'une synthèse /review dans chaque tour de chat.
_REPLY_CONTEXT_MAX_CHARS = 500


async def _send_meal_log(message: Message, meal_log: str | None) -> None:
    """Deuxième message Telegram, séparé de la réponse du coach — trace déterministe de
    ce que `log_meal`/`undo_last_meal_entry` ont réellement écrit en base ce tour (voir
    app/llm/chat.py::_format_meal_ledger). `None` → rien n'est envoyé, aucun outil
    nutrition n'a tourné (décision utilisateur 2026-09-21 : pas de message sur les tours
    qui n'ont rien à voir avec la nutrition)."""
    if not meal_log:
        return
    await message.answer(f"<pre>{escape(meal_log)}</pre>", parse_mode="HTML")


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

    reply_context: str | None = None
    if message.reply_to_message is not None:
        reply_context = message.reply_to_message.text or message.reply_to_message.caption
        if reply_context:
            reply_context = reply_context[:_REPLY_CONTEXT_MAX_CHARS]

    try:
        from app.llm.chat import run_chat
        response_text, intent, tool_used, pending_proposal, usage, meal_log = await run_chat(
            user_message=message.text,
            user=user,
            session=session,
            reply_context=reply_context,
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
        tokens_input=usage.get("prompt_tokens"),
        tokens_output=usage.get("completion_tokens"),
    )

    # spec 010 : une suggestion mode libre confirmable par bouton — volontairement SANS
    # changer l'état FSM (research.md Decision 2) : la négociation ("propose-moi autre
    # chose") doit rester du chat normal, contrairement à PENDING_MODIFICATION ci-dessous
    # qui bloque exprès le chat tant que l'athlète n'a pas tranché.
    if pending_proposal and pending_proposal.get("type") == "freestyle_publish":
        await state.update_data(
            pending_freestyle_id=pending_proposal["id"],
            pending_freestyle_suggestion=pending_proposal,
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📅 Publier sur intervals.icu",
                callback_data=f"freestyle:publish:{pending_proposal['id']}",
            ),
        ]])
        proposal_text = escape(_strip_cjk(response_text)).strip() or _FALLBACK_ERROR
        await message.answer(proposal_text, reply_markup=kb, parse_mode="HTML")
        await _send_meal_log(message, meal_log)
        return

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
        await _send_meal_log(message, meal_log)
        return

    text = escape(_strip_cjk(response_text)).strip()
    if not text:
        text = _FALLBACK_ERROR
    await message.answer(text, parse_mode="HTML")
    await _send_meal_log(message, meal_log)


# ── Message libre pendant une proposition en attente ───────────────────────────
#
# Bug réel trouvé en conditions réelles (2026-08-28) : seuls les callbacks des
# boutons ✅/❌ étaient enregistrés pour PENDING_MODIFICATION. Un message texte
# envoyé dans cet état ne matchait aucun handler (handle_chat_message ci-dessus
# est filtré sur ACTIVE/None) — aiogram l'ignorait silencieusement, sans réponse
# ni erreur visible pour l'athlète.

@router.message(StateFilter(PlanStates.PENDING_MODIFICATION), F.text)
async def handle_message_during_pending_modification(message: Message, state: FSMContext) -> None:
    await message.answer(
        "⏳ Tu as une proposition de modification en attente.\n"
        "Réponds avec ✅ <b>Appliquer</b> ou ❌ <b>Annuler</b> avant de continuer la conversation.",
        parse_mode="HTML",
    )


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


# ── Publication d'une suggestion mode libre (spec 010) ──────────────────────────
#
# Volontairement PAS scopé à un StateFilter particulier (research.md Decision 2) :
# la négociation d'une séance ne fait jamais changer l'état FSM, donc ce callback doit
# pouvoir être tapé à tout moment, quel que soit l'état courant.

@router.callback_query(F.data.startswith("freestyle:publish:"))
async def cb_publish_freestyle(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    tapped_id = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    pending_id = data.get("pending_freestyle_id")
    suggestion = data.get("pending_freestyle_suggestion")

    # FR-005 : une proposition manquante ou remplacée par une plus récente (l'athlète a
    # redemandé autre chose) n'est jamais publiée sous silence.
    if pending_id is None or pending_id != tapped_id or suggestion is None:
        await callback.answer(
            "Cette proposition n'est plus la plus récente — redemande une séance.",
            show_alert=True,
        )
        return

    profile_row = await repo.profile_repo.get_by_user_id(session, user.id)
    if profile_row is None:
        await callback.answer("Profil introuvable — relance /setup.", show_alert=True)
        return

    from datetime import date as _date

    from pydantic import TypeAdapter

    from app.config import settings
    from app.db.repositories import freestyle_publication_repo
    from app.engine.schemas import AthleteProfileSchema, RepeatGroup, Step
    from app.engine.session_render import render_description
    from app.engine.zones import compute_hr_zones, compute_power_zones
    from app.providers.intervals.client import IntervalsClient
    from app.services.publication import publish_freestyle_session

    profile = AthleteProfileSchema.model_validate(profile_row.profile)
    if profile.coaching_mode == "power":
        zones = compute_power_zones(profile.equipment.ftp or 200)
    else:
        zones = compute_hr_zones(profile.physio.hr_max, profile.physio.hr_rest)

    workout_type = suggestion["workout_type"]
    steps = TypeAdapter(list[Step | RepeatGroup]).validate_python(suggestion["steps"])
    name = render_description(workout_type, steps, coaching_mode=profile.coaching_mode)
    session_date = _date.today()

    client = IntervalsClient(
        settings.intervals_api_key.get_secret_value(), athlete_id=settings.intervals_athlete_id
    )
    outcome = await publish_freestyle_session(
        client, session_date, name, workout_type, steps, zones
    )

    if outcome.status != "created":
        # FR-008 : jamais laisser croire que ça a marché ; pending_freestyle_id n'est
        # PAS effacé — retaper est une vraie tentative de nouveau, pas un id périmé.
        await callback.message.edit_text(
            f"❌ La publication n'a pas abouti ({outcome.detail or outcome.status}). "
            "Retente dans un instant."
        )
        await callback.answer()
        return

    await freestyle_publication_repo.create(
        session,
        user_id=user.id,
        external_id=outcome.external_id,
        intervals_event_id=outcome.intervals_event_id,
        session_date=session_date,
        workout_type=workout_type,
        content_hash=outcome.content_hash,
    )
    # FR-010 : effacé seulement après succès — un deuxième tap n'a plus rien à matcher.
    await state.update_data(pending_freestyle_id=None, pending_freestyle_suggestion=None)

    await callback.message.edit_text(
        f"✅ <b>Séance publiée</b> sur ton calendrier intervals.icu pour aujourd'hui "
        f"({session_date:%d/%m}).\nCe n'est pas rattaché à un plan — une séance ponctuelle.",
        parse_mode="HTML",
    )
    await callback.answer()
