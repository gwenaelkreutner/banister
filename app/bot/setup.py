from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.single_user import SingleUserMiddleware
from app.config import settings
from app.core.localization import Language, t


# Ordre = flux d'usage (onboarding puis routine), pas alphabétique — affiché tel quel
# dans le menu "/" de Telegram.
def build_bot_commands(*, language: Language | None = None) -> list[BotCommand]:
    """Build the Telegram menu for the installation's selected language."""
    rows = [
        ("start", "menu.start"),
        ("setup", "menu.setup"),
        ("plan", "menu.plan"),
        ("week", "menu.week"),
        ("fitness", "menu.fitness"),
        ("summary", "menu.summary"),
        ("review", "menu.review"),
        ("goal", "menu.goal"),
        ("publish", "menu.publish"),
        ("unpublish", "menu.unpublish"),
        ("reminders", "menu.reminders"),
        ("voice", "menu.voice"),
        ("reset", "menu.reset"),
        ("cancel", "menu.cancel"),
        ("help", "menu.help"),
    ]
    return [
        BotCommand(
            command=t(f"{key}.command", language=language),
            description=t(f"{key}.description", language=language),
        )
        for _, key in rows
    ]


BOT_COMMANDS = build_bot_commands()


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


async def register_bot_commands(bot: Bot) -> None:
    """Peuple le menu "/" natif de Telegram — sans ça, l'app cliente affiche un menu
    vide même si les commandes fonctionnent (elles sont routées par Command(), pas par
    ce menu). Idempotent — Telegram écrase la liste précédente à chaque appel, donc
    rappelable sans risque à chaque démarrage."""
    await bot.set_my_commands(build_bot_commands())


def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Middlewares (ordre critique : session d'abord, puis guard single-user)
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    dp.message.middleware(SingleUserMiddleware())
    dp.callback_query.middleware(SingleUserMiddleware())

    # Routers (ordre important — chat doit être en dernier : catch-all)
    from app.bot.routers.chat import router as chat_router
    from app.bot.routers.common import router as common_router
    from app.bot.routers.forme import router as forme_router
    from app.bot.routers.goal import router as goal_router
    from app.bot.routers.plan import router as plan_router
    from app.bot.routers.publish import router as publish_router
    from app.bot.routers.recap import router as recap_router
    from app.bot.routers.reminders import router as reminders_router
    from app.bot.routers.reset import router as reset_router
    from app.bot.routers.review import router as review_router
    from app.bot.routers.session_log import router as session_log_router
    from app.bot.routers.setup import router as setup_router
    from app.bot.routers.voice import router as voice_router

    dp.include_router(common_router)
    dp.include_router(setup_router)
    dp.include_router(plan_router)
    dp.include_router(session_log_router)
    dp.include_router(review_router)
    dp.include_router(forme_router)
    dp.include_router(recap_router)
    dp.include_router(reminders_router)
    dp.include_router(publish_router)
    dp.include_router(goal_router)
    dp.include_router(reset_router)
    dp.include_router(voice_router)
    dp.include_router(chat_router)  # doit être en dernier (handler générique)

    return dp
