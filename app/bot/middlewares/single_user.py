from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import PlanStates
from app.config import settings
from app.db import repositories as repo


class SingleUserMiddleware(BaseMiddleware):
    """
    Garde du bot single-user :
    - Rejette silencieusement tout message provenant d'un Telegram ID autre que TELEGRAM_OWNER_ID.
    - Fetche l'utilisateur depuis la DB (sans upsert — créé une seule fois par /setup).
    - Restaure PlanStates.ACTIVE si l'user existe mais que le state FSM a été perdu (redémarrage).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = data.get("session")
        if session is None:
            return await handler(event, data)

        tg_user = None
        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_user = event.from_user

        if tg_user is None:
            data["user"] = None
            return await handler(event, data)

        # Guard : rejeter tout ID non-owner
        if settings.telegram_owner_id and tg_user.id != settings.telegram_owner_id:
            return  # Silently drop

        user = await repo.user_repo.get_by_telegram_id(session, tg_user.id)
        data["user"] = user

        # Restaurer PlanStates.ACTIVE si user existe mais state FSM perdu (redémarrage)
        if user is not None:
            fsm_context: FSMContext | None = data.get("state")
            if fsm_context is not None:
                current_state = await fsm_context.get_state()
                if current_state is None:
                    await fsm_context.set_state(PlanStates.ACTIVE)

        return await handler(event, data)
