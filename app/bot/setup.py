from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.single_user import SingleUserMiddleware
from app.config import settings


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Middlewares (ordre critique : session d'abord, puis guard single-user)
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    dp.message.middleware(SingleUserMiddleware())
    dp.callback_query.middleware(SingleUserMiddleware())

    # Routers (ordre important — chat doit être en dernier : catch-all)
    from app.bot.routers.common import router as common_router
    from app.bot.routers.setup import router as setup_router
    from app.bot.routers.plan import router as plan_router
    from app.bot.routers.session_log import router as session_log_router
    from app.bot.routers.forme import router as forme_router
    from app.bot.routers.recap import router as recap_router
    from app.bot.routers.reminders import router as reminders_router
    from app.bot.routers.publish import router as publish_router
    from app.bot.routers.goal import router as goal_router
    from app.bot.routers.reset import router as reset_router
    from app.bot.routers.voice import router as voice_router
    from app.bot.routers.chat import router as chat_router

    dp.include_router(common_router)
    dp.include_router(setup_router)
    dp.include_router(plan_router)
    dp.include_router(session_log_router)
    dp.include_router(forme_router)
    dp.include_router(recap_router)
    dp.include_router(reminders_router)
    dp.include_router(publish_router)
    dp.include_router(goal_router)
    dp.include_router(reset_router)
    dp.include_router(voice_router)
    dp.include_router(chat_router)  # doit être en dernier (handler générique)

    return dp
