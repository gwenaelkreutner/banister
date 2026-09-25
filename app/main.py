import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.bot.setup import create_bot, create_dispatcher, register_bot_commands
from app.bot.text_format import to_telegram_html
from app.config import settings
from app.core.localization import t
from app.db.client import get_session
from app.db.lifecycle import (
    acquire_instance_lock,
    ensure_data_dir,
    run_migrations,
    verify_intervals_credential,
)
from app.providers.intervals.poller import run_poller_scheduler
from app.services.backup import run_startup_backup

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "loggers": {
        # Silence SQL brut — on veut le flux métier, pas les requêtes
        "sqlalchemy.engine": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "sqlalchemy.pool":   {"level": "WARNING", "handlers": ["console"], "propagate": False},
        # Silence le bruit HTTP bas niveau
        "httpx":    {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "httpcore": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        # Notre app — niveau contrôlé par LOG_LEVEL dans .env (INFO par défaut, DEBUG pour dev)
        "app": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
    },
    "root": {"level": "INFO", "handlers": ["console"]},
})
logger = logging.getLogger(__name__)

bot = create_bot()
dp = create_dispatcher()

# Fuseau des rappels athlète (séance du matin, calories du soir) — un vrai fuseau IANA,
# pas un décalage UTC+1 fixe, pour rester correct pendant l'heure d'été (fin mars-fin
# octobre) sans dériver d'une heure. Athlète unique par instance (principe fondateur),
# donc un seul fuseau pour toute l'app plutôt qu'une colonne par utilisateur.
PARIS_TZ = ZoneInfo("Europe/Paris")

@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = None

    # Doit tourner avant le premier appel LLM (register() patche les SDK
    # anthropic/openai en place) — voir app/observability.py.
    from app.observability import setup_observability
    setup_observability()

    # Storage lifecycle, in order (spec 003): the data directory must exist before the
    # instance lock can be created inside it; the lock must be held before migrations run
    # so two instances can't both migrate or both run schedulers against the same store;
    # migrations must complete before anything else touches the schema (FR-018) — a
    # scheduler or the bot itself hitting a stale schema would surface as an obscure query
    # error rather than the clear startup failure this ordering is meant to produce.
    ensure_data_dir()
    instance_lock = acquire_instance_lock()

    # Snapshot right before migrations run — a failed Alembic migration on SQLite does
    # not roll back automatically the way it would on PostgreSQL (see CLAUDE.md), so a
    # backup taken seconds earlier is what makes that risk recoverable rather than
    # merely "accepted". Blocking sqlite3 call, hence to_thread; never raises — a
    # backup failure must not block startup for a risk that hasn't materialized yet.
    await asyncio.to_thread(
        run_startup_backup, settings.resolved_database_url, settings.data_dir
    )

    await run_migrations()

    # spec 002 FR-002/FR-003: verify the training data source credential before anything
    # else runs. A bad key must surface as a clear startup failure here, not as a
    # mysterious empty result the first time the poller tries to use it.
    await verify_intervals_credential()

    await register_bot_commands(bot)

    if settings.use_webhook:
        webhook_url = f"{settings.telegram_webhook_url}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            drop_pending_updates=True,
        )
        logger.info(f"Webhook configuré : {webhook_url}")
    else:
        # Mode polling (développement) — démarre en tâche background
        await bot.delete_webhook(drop_pending_updates=True)
        polling_task = asyncio.create_task(dp.start_polling(bot))
        logger.info("Mode polling activé (développement)")

    recap_scheduler_task = asyncio.create_task(_weekly_recap_scheduler(bot))
    reminder_scheduler_task = asyncio.create_task(_session_reminder_scheduler(bot))
    nutrition_reminder_scheduler_task = asyncio.create_task(_nutrition_reminder_scheduler(bot))
    poller_scheduler_task = asyncio.create_task(_run_intervals_poller())

    yield

    recap_scheduler_task.cancel()
    try:
        await recap_scheduler_task
    except asyncio.CancelledError:
        pass

    reminder_scheduler_task.cancel()
    try:
        await reminder_scheduler_task
    except asyncio.CancelledError:
        pass

    nutrition_reminder_scheduler_task.cancel()
    try:
        await nutrition_reminder_scheduler_task
    except asyncio.CancelledError:
        pass

    poller_scheduler_task.cancel()
    try:
        await poller_scheduler_task
    except asyncio.CancelledError:
        pass

    if polling_task is not None:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    await bot.session.close()
    instance_lock.release()
    logger.info("Bot arrêté")

app = FastAPI(title="Banister", lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Endpoint webhook Telegram."""
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != settings.telegram_webhook_secret:
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    update_data = await request.json()
    from aiogram.types import Update
    update = Update.model_validate(update_data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

async def _run_intervals_poller() -> None:
    """spec 002 T037/T045. `client_factory` builds a fresh IntervalsClient per tick
    rather than reusing one across the whole app lifetime, matching IntervalsClient's
    own contract (it opens a new httpx.AsyncClient per request already, so there is no
    connection state to keep alive between ticks). Passing the real `bot` is what turns
    this from Phase 5's detection-only loop into Phase 6's live notification path — see
    run_poller_scheduler's docstring for why it is still safe to omit."""
    from app.db.client import AsyncSessionFactory
    from app.providers.intervals.client import IntervalsClient

    def _client_factory() -> IntervalsClient:
        return IntervalsClient(
            settings.intervals_api_key.get_secret_value(),
            athlete_id=settings.intervals_athlete_id,
        )

    await run_poller_scheduler(AsyncSessionFactory, _client_factory, bot=bot)


async def _weekly_recap_scheduler(bot):
    """Envoie le bilan hebdomadaire à tous les utilisateurs actifs chaque dimanche à 20h00 UTC."""
    while True:
        now = datetime.now(UTC)
        days_until_sunday = (6 - now.weekday()) % 7
        next_sunday = now.replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(days=days_until_sunday)
        if next_sunday <= now:
            next_sunday += timedelta(weeks=1)

        wait_seconds = (next_sunday - now).total_seconds()
        logger.info(f"Prochain bilan hebdo dans {wait_seconds / 3600:.1f}h ({next_sunday.strftime('%Y-%m-%d %H:%M UTC')})")
        await asyncio.sleep(wait_seconds)

        try:
            await _run_weekly_recap_broadcast(bot)
        except Exception:
            logger.exception("Erreur lors du bilan hebdomadaire automatique")


async def _run_weekly_recap_broadcast(bot):
    """Envoie le bilan hebdomadaire à tous les utilisateurs actifs."""
    from sqlalchemy import select

    from app.db.client import AsyncSessionFactory
    from app.db.models.user import User
    from app.services.weekly_recap import compute_weekly_recap

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User).where(
                User.is_active == True,
                User.onboarding_completed_at.is_not(None),
            )
        )
        users = list(result.scalars().all())

    logger.info(f"Bilan hebdo : {len(users)} utilisateurs actifs")

    for user in users:
        try:
            async with AsyncSessionFactory() as session:
                async with session.begin():
                    recap = await compute_weekly_recap(session, user)

            if not recap.has_data:
                continue

            await bot.send_message(user.telegram_id, recap.stats_section, parse_mode="HTML")
            await asyncio.sleep(0.8)
            await bot.send_message(
                user.telegram_id,
                t("recap.coach_analysis", analysis=to_telegram_html(recap.coach_section)),
                parse_mode="HTML",
            )
            await asyncio.sleep(0.8)
            nextweek_footer = (
                t("recap.next_week_footer", week=recap.next_week_number)
                if recap.next_week_number
                else ""
            )
            await bot.send_message(
                user.telegram_id,
                t(
                    "recap.next_week",
                    section=to_telegram_html(recap.next_week_section),
                    footer=nextweek_footer,
                ),
                parse_mode="HTML",
            )
            await asyncio.sleep(0.1)  # rate limit entre utilisateurs
        except Exception:
            logger.exception(f"Erreur bilan hebdo user {user.telegram_id}")


def _format_reminder(session_spec, week_num: int, weeks_count: int) -> str:
    from app.bot.routers.session_log import _type_label

    zone = session_spec.zone_code
    duration = session_spec.duration_minutes
    target = session_spec.target_time_in_zone_minutes or session_spec.duration_minutes
    return t(
        "main.session_reminder",
        week=week_num,
        weeks_count=weeks_count,
        type=_type_label(session_spec.workout_type),
        zone=zone,
        duration=duration,
        target=target,
    )


async def _session_reminder_scheduler(bot):
    """Envoie les rappels de séance matinaux toutes les minutes (heure de Paris)."""
    while True:
        await asyncio.sleep(60)
        try:
            await _run_session_reminders(bot)
        except Exception:
            logger.exception("Erreur lors des rappels de séance")


async def _run_session_reminders(bot):
    """Vérifie l'heure de Paris et envoie les rappels dus."""
    from app.db import repositories as repo
    from app.db.client import AsyncSessionFactory
    from app.engine.schemas import TrainingPlanSchema

    now_paris = datetime.now(PARIS_TZ)
    today = now_paris.date()

    async with AsyncSessionFactory() as session:
        users = await repo.user_repo.get_users_to_remind(session, now_paris.hour, now_paris.minute)
        pending = [(u.telegram_id, u.id) for u in users]

    if not pending:
        return

    logger.info(
        f"Rappels séance {now_paris.hour:02d}:{now_paris.minute:02d} "
        f"{now_paris.tzname()} : {len(pending)} utilisateur(s)"
    )

    for telegram_id, user_id in pending:
        try:
            text = None
            async with AsyncSessionFactory() as session:
                async with session.begin():
                    user = await repo.user_repo.get_by_telegram_id(session, telegram_id)
                    if not user:
                        continue

                    db_plan = await repo.plan_repo.get_active_plan(session, user.id)
                    if db_plan:
                        plan = TrainingPlanSchema.model_validate(db_plan.plan_technical)
                        days_elapsed = (today - db_plan.start_date).days
                        week_num = min(days_elapsed // 7 + 1, plan.weeks_count)
                        week = next((w for w in plan.weeks if w.week_number == week_num), None)
                        if week:
                            session_today = next(
                                (s for s in week.sessions if s.day_of_week == today.weekday()),
                                None,
                            )
                            if session_today:
                                text = _format_reminder(session_today, week_num, plan.weeks_count)

                    # Marquer comme traité aujourd'hui (même si jour de repos)
                    user.reminder_last_sent_at = today

            if text:
                await bot.send_message(telegram_id, text, parse_mode="HTML")
                await asyncio.sleep(0.1)  # rate limit entre utilisateurs
        except Exception:
            logger.exception(f"Erreur rappel séance user {telegram_id}")


def _format_nutrition_reminder() -> str:
    return t("main.nutrition_reminder")


async def _nutrition_reminder_scheduler(bot):
    """Envoie un rappel calorique quotidien à 22h00 heure de Paris si rien n'a été
    loggé ce jour-là (spec 008 US5). Anciennement un décalage UTC+1 fixe (voir
    research R4 de spec 008) — dérivait d'une heure pendant l'heure d'été (fin
    mars-fin octobre) ; corrigé ici en même temps que `_run_session_reminders`
    puisque le fix touche forcément les deux rappels à la fois (backlog)."""
    while True:
        now_paris = datetime.now(PARIS_TZ)
        next_run = now_paris.replace(hour=22, minute=0, second=0, microsecond=0)
        if next_run <= now_paris:
            next_run += timedelta(days=1)

        wait_seconds = (next_run - now_paris).total_seconds()
        logger.info(
            f"Prochain rappel calories dans {wait_seconds / 3600:.1f}h "
            f"({next_run.strftime('%Y-%m-%d %H:%M')} {next_run.tzname()})"
        )
        await asyncio.sleep(wait_seconds)

        try:
            await _run_nutrition_reminders(bot)
        except Exception:
            logger.exception("Erreur lors des rappels caloriques")


async def _run_nutrition_reminders(bot):
    """Envoie le rappel à chaque utilisateur actif n'ayant rien loggé aujourd'hui."""
    from sqlalchemy import select

    from app.db.client import AsyncSessionFactory
    from app.db.models.user import User
    from app.services.nutrition_reminder import needs_reminder

    today = date.today()

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(User).where(
                User.is_active,
                User.onboarding_completed_at.is_not(None),
            )
        )
        users = list(result.scalars().all())

    pending: list[int] = []
    for user in users:
        async with AsyncSessionFactory() as session:
            if await needs_reminder(session, user.id, today):
                pending.append(user.telegram_id)

    if not pending:
        return

    logger.info(f"Rappel calories : {len(pending)} utilisateur(s)")
    text = _format_nutrition_reminder()
    for telegram_id in pending:
        try:
            await bot.send_message(telegram_id, text, parse_mode="HTML")
            await asyncio.sleep(0.1)  # rate limit entre utilisateurs
        except Exception:
            logger.exception(f"Erreur rappel calories user {telegram_id}")


async def run_polling():
    """Lance le bot en mode polling (développement)."""
    logger.info("Démarrage du polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_polling())
