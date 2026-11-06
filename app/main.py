import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.agent import router as agent_router
from app.api.internal import router as internal_router
from app.bot.setup import create_bot, create_dispatcher, register_bot_commands
from app.bot.text_format import to_telegram_html
from app.services.outbox import OutboxMessenger
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

bot = None if settings.uses_agent_interface else create_bot()
dp = create_dispatcher()
# Who carries Banister's messages: its own bot, or the outbox an external agent delivers from.
messenger = OutboxMessenger() if settings.uses_agent_interface else bot

# Fuseau des rappels athlète (séance du matin) — un vrai fuseau IANA,
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

    if settings.uses_agent_interface:
        # The agent talks to the athlete. Banister's bot must not answer, or take updates.
        logger.info("Interface agent : bot Telegram inactif, messages via l'outbox")
    else:
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

    recap_scheduler_task = asyncio.create_task(_weekly_recap_scheduler(messenger))
    reminder_scheduler_task = asyncio.create_task(_session_reminder_scheduler(messenger))
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
    if bot is not None:
        await bot.session.close()
    instance_lock.release()
    logger.info("Bot arrêté")

app = FastAPI(title="Banister", lifespan=lifespan)
app.include_router(internal_router)
app.include_router(agent_router)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Endpoint webhook Telegram."""
    if settings.uses_agent_interface:
        return JSONResponse({"error": "not found"}, status_code=404)
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

    await run_poller_scheduler(AsyncSessionFactory, _client_factory, bot=messenger)


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
    from app.services.agent_actions import WEEKLY_RECAP_PROMPT
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
                    recap = await compute_weekly_recap(
                        session, user, narrate=not settings.uses_agent_interface
                    )

            if not recap.has_data:
                continue

            if settings.uses_agent_interface:
                # The figures from here; the analysis in the agent's own words.
                await bot.send_message(
                    user.telegram_id,
                    recap.stats_section,
                    parse_mode="HTML",
                    agent_prompt=WEEKLY_RECAP_PROMPT,
                )
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


async def run_polling():
    """Lance le bot en mode polling (développement)."""
    logger.info("Démarrage du polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_polling())
