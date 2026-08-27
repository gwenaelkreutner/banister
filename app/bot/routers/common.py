from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.states import PlanStates

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, user) -> None:
    if user is None:
        # First contact — no user in DB yet
        await message.answer(
            "👋 Bienvenue sur <b>Banister</b> !\n\n"
            "Lance /setup pour configurer ton profil et générer ton plan d'entraînement.",
            parse_mode="HTML",
        )
        return

    # User exists → already configured
    await state.set_state(PlanStates.ACTIVE)
    await message.answer(
        f"Bon retour, {user.first_name or 'Champion'} ! 🚴\n"
        "Tape /plan pour voir ton programme de la semaine.\n"
        "Tape /setup pour reconfigurer ton profil.",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Rien à annuler.")
        return
    await state.clear()
    await message.answer("❌ Opération annulée.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Banister — Aide</b>\n\n"
        "• /setup — Configurer ton profil et générer un plan\n"
        "• /plan — Voir ton programme de la semaine\n"
        "• /week N — Voir la semaine N de ton plan\n"
        "• /forme — Voir tes métriques de forme (CTL/ATL/TSB)\n"
        "• /recap — Récapitulatif hebdomadaire\n"
        "• /reminders — Gérer les rappels de séance\n"
        "• /cancel — Annuler l'action en cours\n\n"
        "Tu peux aussi me poser des questions librement ! 💬",
        parse_mode="HTML",
    )
