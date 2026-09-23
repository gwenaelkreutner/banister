from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.single_user import SingleUserMiddleware
from app.config import settings

# Ordre = flux d'usage (onboarding puis routine), pas alphabétique — affiché tel quel
# dans le menu "/" de Telegram.
BOT_COMMANDS = [
    BotCommand(command="start", description="Démarrer / accueil"),
    BotCommand(command="setup", description="Configurer ton profil et générer un plan"),
    BotCommand(command="plan", description="Voir ton programme de la semaine"),
    BotCommand(command="week", description="Voir une semaine du plan (ex: /week 3)"),
    BotCommand(command="forme", description="Métriques de forme (CTL/ATL/TSB)"),
    BotCommand(command="recap", description="Récapitulatif hebdomadaire"),
    BotCommand(command="review", description="Analyse complète d'une séance passée"),
    BotCommand(command="goal", description="Changer d'objectif et régénérer le plan"),
    BotCommand(command="publish", description="Publier les 2 prochaines semaines au calendrier"),
    BotCommand(command="unpublish", description="Retirer les séances publiées"),
    BotCommand(command="reminders", description="Gérer les rappels de séance"),
    BotCommand(command="voice", description="Choisir la voix de ton coach"),
    BotCommand(command="reset", description="Effacer profil + historique local (garde intervals.icu)"),
    BotCommand(command="cancel", description="Interrompre /setup, /goal ou /reset en cours"),
    BotCommand(command="help", description="Aide"),
]


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


async def register_bot_commands(bot: Bot) -> None:
    """Peuple le menu "/" natif de Telegram — sans ça, l'app cliente affiche un menu
    vide même si les commandes fonctionnent (elles sont routées par Command(), pas par
    ce menu). Idempotent — Telegram écrase la liste précédente à chaque appel, donc
    rappelable sans risque à chaque démarrage."""
    await bot.set_my_commands(BOT_COMMANDS)


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
    from app.bot.routers.review import router as review_router
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
