from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.client import AsyncSessionFactory


class DbSessionMiddleware(BaseMiddleware):
    """Injecte une AsyncSession dans data["session"] pour chaque handler."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with AsyncSessionFactory() as session:
            async with session.begin():
                data["session"] = session
                return await handler(event, data)
