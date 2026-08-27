import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from html import escape
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.setup import create_bot, create_dispatcher
from app.config import settings
from app.db.client import get_session
from app.db.lifecycle import (
    acquire_instance_lock,
    ensure_data_dir,
    run_migrations,
    verify_intervals_credential,
)
from app.providers.intervals.poller import run_poller_scheduler

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    polling_task = None

    # Storage lifecycle, in order (spec 003): the data directory must exist before the
    # instance lock can be created inside it; the lock must be held before migrations run
    # so two instances can't both migrate or both run schedulers against the same store;
    # migrations must complete before anything else touches the schema (FR-018) — a
    # scheduler or the bot itself hitting a stale schema would surface as an obscure query
    # error rather than the clear startup failure this ordering is meant to produce.
    ensure_data_dir()
    instance_lock = acquire_instance_lock()
    await run_migrations()

    # spec 002 FR-002/FR-003: verify the training data source credential before anything
    # else runs. A bad key must surface as a clear startup failure here, not as a
    # mysterious empty result the first time the poller tries to use it.
    await verify_intervals_credential()

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
    await bot.session.close()
    instance_lock.release()
    logger.info("Bot arrêté")

app = FastAPI(title="Banister", lifespan=lifespan)

class DevStravaSimulatePayload(BaseModel):
    owner_id: int = Field(..., description="Athlete Strava id (oauth_connections.provider_user_id)")
    object_id: int = Field(..., description="Strava activity id simulé")
    activity: dict[str, Any] = Field(..., description="Payload activité Strava-like complet")

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

@app.get("/auth/strava/callback")
async def strava_oauth_callback(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_session),
):
    """Callback OAuth Strava — échange le code contre des tokens et notifie l'utilisateur."""
    from app.db import repositories as repo
    from app.strava.oauth import exchange_code, verify_state

    result = verify_state(state)
    if result is None:
        return HTMLResponse("<h2>❌ Lien invalide ou expiré.</h2>", status_code=400)
    telegram_id, context = result

    user = await repo.user_repo.get_by_telegram_id(session, telegram_id)
    if user is None:
        return HTMLResponse("<h2>❌ Utilisateur introuvable.</h2>", status_code=404)

    try:
        tokens = await exchange_code(code)
    except Exception:
        logger.exception("Erreur lors de l'échange du code Strava")
        return HTMLResponse("<h2>❌ Erreur lors de la connexion Strava. Réessaie.</h2>", status_code=502)

    provider_user_id = str(tokens.get("athlete", {}).get("id", ""))
    await repo.oauth_repo.upsert_connection(session, user.id, "strava", tokens, provider_user_id)
    await session.commit()

    if context == "onboarding":
        access_token = tokens.get("access_token", "")
        await bot.send_message(
            telegram_id,
            "✅ *Strava connecté !* Import de tes activités en cours...\n\n"
            "_Cela peut prendre quelques secondes._",
            parse_mode="Markdown",
        )
        asyncio.create_task(_run_strava_onboarding_import(telegram_id, user.id, access_token))
    else:
        await bot.send_message(
            telegram_id,
            "✅ *Strava connecté avec succès !*\n\nTu peux fermer cette page.",
            parse_mode="Markdown",
        )

    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        "<h2>✅ Strava connecté !</h2>"
        "<p>Tu peux fermer cette fenêtre et revenir sur Telegram.</p>"
        "</body></html>"
    )

async def _run_strava_onboarding_import(telegram_id: int, user_id, access_token: str):
    """Importe l'historique Strava et stocke l'analyse dans onboarding_state."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from app.db import repositories as repo
    from app.db.client import AsyncSessionFactory
    from app.strava.history import generate_strava_intro, import_history

    try:
        async with AsyncSessionFactory() as session:
            async with session.begin():
                analysis = await import_history(session, user_id, access_token)

                onboarding = await repo.onboarding_repo.get_or_create(session, user_id)
                data = dict(onboarding.session_data)
                data["strava_analysis"] = analysis
                data.update({
                    "level":                   analysis["level_detected"],
                    "volume_suggested":        analysis["volume_suggested"],
                    # hours_per_week intentionnellement absent — saisi par l'utilisateur à l'étape 3
                    "power_meter":             analysis["has_power_meter"],
                    "ftp":                     analysis.get("ftp_detected"),
                    "ftp_source":              "declared" if analysis.get("ftp_detected") else "estimated",
                    "hr_max":                  analysis.get("hr_max_detected"),
                    "hr_max_source":           "declared" if analysis.get("hr_max_detected") else "estimated",
                    "hr_rest":                 60,
                    "hr_rest_source":          "estimated",
                    "age":                     35,
                    "sex":                     analysis.get("athlete_sex"),
                    "weight_kg":               analysis.get("athlete_weight_kg"),
                    "structured_plan_history": analysis.get("volume_suggested", 0) > 5,
                })
                await repo.onboarding_repo.update_step(session, onboarding, 0, data)

        # Générer l'intro narrative LLM
        intro_text = await generate_strava_intro(analysis)

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="➤ Continuer l'onboarding",
                callback_data="strava:continue_onboarding",
            )
        ]])
        await bot.send_message(telegram_id, escape(intro_text), parse_mode="HTML")
        await bot.send_message(
            telegram_id,
            "Ces données sont intégrées automatiquement. Plus que 5 questions !",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("Erreur import historique Strava onboarding")
        await bot.send_message(
            telegram_id,
            "⚠️ L'import Strava a rencontré un problème. "
            "Tu peux continuer sans les données automatiques — tape /start.",
        )

@app.post("/dev/strava/simulate-activity")
async def dev_strava_simulate_activity(payload: DevStravaSimulatePayload):
    """Simule un event webhook Strava en environnement de développement uniquement."""
    if not settings.is_dev:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    from app.strava.webhook import handle_activity_event

    event = {
        "object_type": "activity",
        "aspect_type": "create",
        "owner_id": payload.owner_id,
        "object_id": payload.object_id,
    }
    asyncio.create_task(handle_activity_event(event, bot, injected_activity=payload.activity))
    return JSONResponse({"ok": True, "simulated": True})

@app.get("/auth/strava/webhook")
async def strava_webhook_verify(request: Request):
    """Validation de la souscription webhook Strava (challenge)."""
    params = dict(request.query_params)
    if params.get("hub.verify_token") != settings.strava_webhook_verify_token:
        return JSONResponse({"error": "invalid verify_token"}, status_code=403)
    return JSONResponse({"hub.challenge": params.get("hub.challenge", "")})

@app.post("/auth/strava/webhook")
async def strava_webhook_event(request: Request):
    """Réception des événements d'activité Strava."""
    try:
        event = await request.json()
        # Traitement asynchrone sans bloquer la réponse (Strava attend < 2s)
        import asyncio

        from app.strava.webhook import handle_activity_event
        asyncio.create_task(handle_activity_event(event, bot))
    except Exception:
        logger.exception("Erreur traitement webhook Strava")
    return JSONResponse({"ok": True})

async def _run_intervals_poller() -> None:
    """spec 002 T037. Detection only for now (Phase 5) — see poller.py's module
    docstring. `client_factory` builds a fresh IntervalsClient per tick rather than
    reusing one across the whole app lifetime, matching IntervalsClient's own contract
    (it opens a new httpx.AsyncClient per request already, so there is no connection
    state to keep alive between ticks)."""
    from app.db.client import AsyncSessionFactory
    from app.providers.intervals.client import IntervalsClient

    def _client_factory() -> IntervalsClient:
        return IntervalsClient(
            settings.intervals_api_key.get_secret_value(),
            athlete_id=settings.intervals_athlete_id,
        )

    await run_poller_scheduler(AsyncSessionFactory, _client_factory)


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
                f"🧠 <b>Analyse coach</b>\n\n{escape(recap.coach_section)}",
                parse_mode="HTML",
            )
            await asyncio.sleep(0.8)
            nextweek_footer = (
                f"\n\n📅 <i>Tape /week {recap.next_week_number} pour voir le détail complet</i>"
                if recap.next_week_number
                else ""
            )
            await bot.send_message(
                user.telegram_id,
                f"🎯 <b>Semaine prochaine</b>\n\n{escape(recap.next_week_section)}{nextweek_footer}",
                parse_mode="HTML",
            )
            await asyncio.sleep(0.1)  # rate limit entre utilisateurs
        except Exception:
            logger.exception(f"Erreur bilan hebdo user {user.telegram_id}")


_WORKOUT_FR = {
    "long_ride": "Sortie longue",
    "intervals": "Intervalles",
    "endurance": "Endurance",
    "recovery": "Récupération",
}


def _format_reminder(session_spec, week_num: int, weeks_count: int) -> str:
    type_fr = _WORKOUT_FR.get(session_spec.workout_type, session_spec.workout_type)
    zone = session_spec.zone_code
    duration = session_spec.duration_minutes
    target = session_spec.target_time_in_zone_minutes or session_spec.duration_minutes
    return (
        f"🚴 <b>Séance du jour</b> — Semaine {week_num}/{weeks_count}\n\n"
        f"<b>{type_fr}</b> {zone} — {duration} min\n"
        f"Objectif : {target} min en {zone}\n\n"
        f"📅 /plan pour voir le programme complet\n"
        f"💬 Une question sur ta séance ? Pose-la ici !"
    )


async def _session_reminder_scheduler(bot):
    """Envoie les rappels de séance matinaux toutes les minutes (UTC+1)."""
    while True:
        await asyncio.sleep(60)
        try:
            await _run_session_reminders(bot)
        except Exception:
            logger.exception("Erreur lors des rappels de séance")


async def _run_session_reminders(bot):
    """Vérifie l'heure CET et envoie les rappels dus."""
    from datetime import timedelta

    from app.db import repositories as repo
    from app.db.client import AsyncSessionFactory
    from app.engine.schemas import TrainingPlanSchema

    CET = timezone(timedelta(hours=1))
    now_cet = datetime.now(CET)
    today = now_cet.date()

    async with AsyncSessionFactory() as session:
        users = await repo.user_repo.get_users_to_remind(session, now_cet.hour, now_cet.minute)
        pending = [(u.telegram_id, u.id) for u in users]

    if not pending:
        return

    logger.info(f"Rappels séance {now_cet.hour:02d}:{now_cet.minute:02d} CET : {len(pending)} utilisateur(s)")

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
