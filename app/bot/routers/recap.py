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
from app.core.localization import t
from app.db.models.user import User
from app.services.weekly_recap import compute_weekly_recap

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("recap", "summary"))
async def cmd_recap(message: Message, session: AsyncSession, user: User):
    if not user.onboarding_completed:
        await message.answer(
            t("recap.complete_setup")
        )
        return

    loading = await message.answer(
        t("recap.analyzing"),
        parse_mode="HTML",
    )

    try:
        recap = await compute_weekly_recap(session, user)
    except Exception:
        logger.exception("Erreur lors du calcul du récap hebdomadaire")
        await loading.delete()
        await message.answer(
            t("recap.error")
        )
        return

    await loading.delete()

    if not recap.has_data:
        await message.answer(
            t("recap.no_data")
        )
        return

    await message.answer(recap.stats_section, parse_mode="HTML")
    await asyncio.sleep(0.8)
    await message.answer(
        t("recap.coach_analysis", analysis=to_telegram_html(recap.coach_section)),
        parse_mode="HTML",
    )
    await asyncio.sleep(0.8)

    nextweek_footer = (
        t("recap.next_week_footer", week=recap.next_week_number)
        if recap.next_week_number
        else ""
    )
    await message.answer(
        t("recap.next_week", section=to_telegram_html(recap.next_week_section), footer=nextweek_footer),
        parse_mode="HTML",
    )
