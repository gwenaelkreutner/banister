from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.states import PlanStates
from app.core.localization import t

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, user) -> None:
    if user is None:
        # First contact — no user in DB yet
        await message.answer(
            t("common.welcome_new"),
            parse_mode="HTML",
        )
        return

    # User exists → already configured
    await state.set_state(PlanStates.ACTIVE)
    await message.answer(
        t("common.welcome_back", name=user.first_name or "Champion"),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(t("common.nothing_to_cancel"))
        return
    await state.clear()
    await message.answer(t("common.operation_cancelled"))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        t("common.help"),
        parse_mode="HTML",
    )
