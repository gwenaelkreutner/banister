"""
Router : /recap — bilan hebdomadaire avec analyse LLM.

Envoie 3 messages séquentiels :
  1. Stats de la semaine (déterministe)
  2. Analyse coach (LLM)
  3. Recommandations semaine prochaine (LLM)
"""

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.text_format import to_telegram_html
from app.db.models.user import User
from app.services.weekly_recap import compute_weekly_recap

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("recap"))
async def cmd_recap(message: Message, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer("Complète d'abord ton onboarding avec /start.")
        return

    loading = await message.answer("💬 <i>Analyse de ta semaine en cours...</i>", parse_mode="HTML")

    try:
        recap = await compute_weekly_recap(session, user)
    except Exception:
        logger.exception("Erreur lors du calcul du récap hebdomadaire")
        await loading.delete()
        await message.answer("⚠️ Une erreur est survenue. Réessaie dans quelques instants.")
        return

    await loading.delete()

    if not recap.has_data:
        await message.answer(
            "📊 Pas encore de données.\n\n"
            "Log tes premières séances avec /log pour voir ton bilan hebdo !"
        )
        return

    await message.answer(recap.stats_section, parse_mode="HTML")
    await asyncio.sleep(0.8)
    await message.answer(
        f"🧠 <b>Analyse coach</b>\n\n{to_telegram_html(recap.coach_section)}",
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)

    nextweek_footer = (
        f"\n\n📅 <i>Tape /week {recap.next_week_number} pour voir le détail complet</i>"
        if recap.next_week_number
        else ""
    )
    await message.answer(
        f"🎯 <b>Semaine prochaine</b>\n\n{to_telegram_html(recap.next_week_section)}{nextweek_footer}",
        parse_mode="HTML",
    )
