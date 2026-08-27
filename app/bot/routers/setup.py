"""
Commande /setup — configuration initiale et régénération du plan.

Flow FSM (SetupStates) :
  SPORT → GOAL → DATE → VOLUME → POWER → AGE → génération du plan

Peut être relancé à tout moment : /setup repart depuis le début et régénère le plan.
"""

from datetime import UTC, date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import PlanStates, SetupStates
from app.db import repositories as repo
from app.db.repositories import activity_repo
from app.engine.atl_ctl import compute_fitness_from_any, estimate_initial_ctl
from app.services.fitness import get_current_fitness
from app.engine.plan_builder import generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.engine.tss import tss_from_weekly_hours

router = Router()

# ── Keyboards ──────────────────────────────────────────────────────────────────

def _kb(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in buttons]
    )


def sport_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("🚴 Cyclisme", "setup:sport:cycling"),
        ("🏃 Course à pied", "setup:sport:running"),
        ("🏊 Triathlon", "setup:sport:triathlon"),
        ("🏅 Autre sport", "setup:sport:other"),
    ])


def goal_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("🎯 Événement cible", "setup:goal:event"),
        ("💚 Forme générale", "setup:goal:fitness"),
        ("⚡ Performance", "setup:goal:performance"),
        ("🔹 Autre", "setup:goal:other"),
    ])


def volume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5h", callback_data="setup:vol:5"),
            InlineKeyboardButton(text="7h", callback_data="setup:vol:7"),
            InlineKeyboardButton(text="10h", callback_data="setup:vol:10"),
        ],
        [
            InlineKeyboardButton(text="12h", callback_data="setup:vol:12"),
            InlineKeyboardButton(text="15h", callback_data="setup:vol:15"),
        ],
    ])


def power_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("⚡ Oui, j'ai un capteur de puissance", "setup:power:yes"),
        ("❤️ Non, j'utilise la fréquence cardiaque", "setup:power:no"),
    ])


# ── Entry point ────────────────────────────────────────────────────────────────

@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SetupStates.SPORT)
    await message.answer(
        "⚙️ <b>Configuration de Banister</b>\n\n"
        "Quelques questions pour calibrer ton plan d'entraînement.\n\n"
        "Quel est ton sport principal ?",
        parse_mode="HTML",
        reply_markup=sport_keyboard(),
    )


# ── Step 1 : Sport ─────────────────────────────────────────────────────────────

@router.callback_query(SetupStates.SPORT, F.data.startswith("setup:sport:"))
async def setup_sport(callback: CallbackQuery, state: FSMContext) -> None:
    sport = callback.data.split(":")[2]
    await state.update_data(sport=sport)

    if sport != "cycling":
        await callback.message.edit_text(
            "ℹ️ Les sports autres que le cyclisme seront mieux pris en charge dans les prochaines versions.\n"
            "Le plan généré utilisera les mêmes principes de charge (TSS/CTL/ATL).\n\n"
            "Quel est ton objectif ?",
            reply_markup=goal_keyboard(),
        )
    else:
        await callback.message.edit_text(
            "Quel est ton objectif ?",
            reply_markup=goal_keyboard(),
        )
    await state.set_state(SetupStates.GOAL)
    await callback.answer()


# ── Step 2 : Goal ──────────────────────────────────────────────────────────────

@router.callback_query(SetupStates.GOAL, F.data.startswith("setup:goal:"))
async def setup_goal(callback: CallbackQuery, state: FSMContext) -> None:
    goal = callback.data.split(":")[2]
    await state.update_data(goal=goal)
    await state.set_state(SetupStates.DATE)
    await callback.message.edit_text(
        "📅 As-tu un événement cible ?\n\n"
        "Réponds avec la date au format <code>AAAA-MM-JJ</code> (ex: <code>2026-09-15</code>)\n"
        "ou tape <code>aucune</code> si tu n'as pas d'échéance.",
        parse_mode="HTML",
    )
    await callback.answer()


# ── Step 3 : Target date ───────────────────────────────────────────────────────

@router.message(SetupStates.DATE)
async def setup_date(message: Message, state: FSMContext) -> None:
    raw = message.text.strip().lower()
    target_date = None

    if raw not in ("aucune", "none", "-", "skip"):
        try:
            target_date = date.fromisoformat(message.text.strip())
            if target_date <= date.today():
                await message.answer(
                    "⚠️ La date doit être dans le futur. Réessaie (format <code>AAAA-MM-JJ</code>) "
                    "ou tape <code>aucune</code>.",
                    parse_mode="HTML",
                )
                return
        except ValueError:
            await message.answer(
                "⚠️ Format non reconnu. Utilise <code>AAAA-MM-JJ</code> (ex: <code>2026-09-15</code>) "
                "ou tape <code>aucune</code>.",
                parse_mode="HTML",
            )
            return

    await state.update_data(target_date=target_date.isoformat() if target_date else None)
    await state.set_state(SetupStates.VOLUME)
    await message.answer(
        "🕐 Combien d'heures par semaine peux-tu consacrer à l'entraînement ?",
        reply_markup=volume_keyboard(),
    )


# ── Step 4 : Weekly volume ─────────────────────────────────────────────────────

@router.callback_query(SetupStates.VOLUME, F.data.startswith("setup:vol:"))
async def setup_volume(callback: CallbackQuery, state: FSMContext) -> None:
    hours = float(callback.data.split(":")[2])
    await state.update_data(hours_per_week=hours)
    await state.set_state(SetupStates.POWER)
    await callback.message.edit_text(
        "📊 As-tu un capteur de puissance (ou de vitesse/allure pour la course) ?",
        reply_markup=power_keyboard(),
    )
    await callback.answer()


# ── Step 5 : Power meter ───────────────────────────────────────────────────────

@router.callback_query(SetupStates.POWER, F.data == "setup:power:yes")
async def setup_power_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(has_power_meter=True)
    await state.set_state(SetupStates.AGE)
    await callback.message.edit_text(
        "⚡ Quel est ton FTP actuel (en watts) ?\n\n"
        "Réponds avec un nombre (ex: <code>260</code>), "
        "ou <code>?</code> si tu ne sais pas — je l'estimerai.",
        parse_mode="HTML",
    )
    await callback.answer()
    # Store a flag so AGE handler knows to also collect FTP
    await state.update_data(_awaiting_ftp=True)


@router.callback_query(SetupStates.POWER, F.data == "setup:power:no")
async def setup_power_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(has_power_meter=False)
    await state.set_state(SetupStates.AGE)
    await callback.message.edit_text(
        "❤️ Quelle est ta fréquence cardiaque maximale (en bpm) ?\n\n"
        "Réponds avec un nombre (ex: <code>183</code>), "
        "ou <code>?</code> si tu ne sais pas — je l'estimerai.",
        parse_mode="HTML",
    )
    await callback.answer()
    await state.update_data(_awaiting_ftp=False)


# ── Step 5b : FTP or HR max (text input) ──────────────────────────────────────

@router.message(SetupStates.AGE, F.text)
async def setup_age_or_metric(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    fsm_data = await state.get_data()
    awaiting_ftp: bool = fsm_data.get("_awaiting_ftp", False)
    has_ftp_or_hr = "ftp" in fsm_data or "hr_max" in fsm_data

    # First text input in AGE state = FTP or HR max
    if not has_ftp_or_hr:
        raw = message.text.strip()
        if awaiting_ftp:
            if raw == "?":
                await state.update_data(ftp=None, ftp_source="estimated")
            else:
                try:
                    ftp = int(raw)
                    if not (50 <= ftp <= 600):
                        raise ValueError
                    await state.update_data(ftp=ftp, ftp_source="declared")
                except ValueError:
                    await message.answer("⚠️ Valeur incorrecte. Entre un FTP en watts (ex: <code>260</code>) ou <code>?</code>.", parse_mode="HTML")
                    return
        else:
            if raw == "?":
                await state.update_data(hr_max=None, hr_max_source="estimated")
            else:
                try:
                    hr = int(raw)
                    if not (100 <= hr <= 230):
                        raise ValueError
                    await state.update_data(hr_max=hr, hr_max_source="declared")
                except ValueError:
                    await message.answer("⚠️ Valeur incorrecte. Entre ta FC max en bpm (ex: <code>183</code>) ou <code>?</code>.", parse_mode="HTML")
                    return

        await message.answer(
            "👤 Quel est ton âge ? (ex: <code>38</code>)",
            parse_mode="HTML",
        )
        return

    # Second text input = Age → final step, generate plan
    try:
        age = int(message.text.strip())
        if not (14 <= age <= 90):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Âge invalide. Entre un nombre entier (ex: <code>38</code>).", parse_mode="HTML")
        return

    await state.update_data(age=age)
    await message.answer("⏳ Génération de ton plan en cours...")

    fsm_data = await state.get_data()
    await _finalize_setup(message, state, session, user, fsm_data)


# ── Finalization : create user, build profile, generate plan ───────────────────

async def _finalize_setup(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user,
    data: dict,
) -> None:
    from app.config import settings

    # Create or reuse user record
    if user is None:
        tg = message.from_user
        user = await repo.user_repo.create_single_user(
            session,
            telegram_id=tg.id,
            username=tg.username,
            first_name=tg.first_name,
        )

    # Forme de départ : consommée depuis la source si déjà disponible (spec 002 FR-016),
    # sinon repli sur l'historique local importé — les deux peuvent être vides si
    # l'athlète vient tout juste de se connecter et que le poller n'a pas encore eu son
    # premier tick (import_history/ingest_wellness sont idempotents, voir poller.py)
    fitness = None
    try:
        current = await get_current_fitness(session, user.id)
        if current is not None:
            fitness = current.metrics
        else:
            activities = await activity_repo.get_for_user(session, user.id, days=120)
            if activities:
                weekly_tss_seed = tss_from_weekly_hours(float(data.get("hours_per_week", 5)))
                initial_ctl_seed = estimate_initial_ctl(weekly_tss_seed)
                seed_date = date.today() - timedelta(days=120)
                fitness = compute_fitness_from_any(
                    activities, initial_ctl=initial_ctl_seed, seed_date=seed_date
                )
    except Exception:
        pass

    profile = _build_profile(data, fitness)
    plan = generate_plan(profile)

    plan_dict = plan.model_dump(mode="json")
    start_date = plan.start_date or date.today()
    end_date = plan.end_date or date.today()

    # Deactivate old plans, then create new one
    await repo.plan_repo.create(
        session=session,
        user_id=user.id,
        plan_technical=plan_dict,
        start_date=start_date,
        end_date=end_date,
    )

    # Save/overwrite athlete profile
    profile_data = profile.model_dump(mode="json")
    existing_profile = await repo.profile_repo.get_by_user_id(session, user.id)
    if existing_profile is None:
        await repo.profile_repo.create(session, user.id, profile_data)
    else:
        await repo.profile_repo.update(session, existing_profile, profile_data)

    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(UTC)

    await state.set_state(PlanStates.ACTIVE)
    await state.clear()

    summary = _build_plan_summary(plan)
    await message.answer(
        f"🎉 <b>Ton plan est prêt !</b>\n\n{summary}\n\n"
        "Tape /plan pour voir ta première semaine en détail. 🚴",
        parse_mode="HTML",
    )

    # LLM narrative in background (non-blocking)
    import asyncio
    asyncio.create_task(_generate_narrative(
        user.id, plan, profile,
        bot=message.bot,
        chat_id=message.chat.id,
    ))


def _build_profile(data: dict, fitness=None) -> AthleteProfileSchema:
    has_pm = data.get("has_power_meter", False)
    ftp = data.get("ftp")
    age = data.get("age", 35)
    hr_max = data.get("hr_max")
    if hr_max is None:
        hr_max = 220 - age

    if has_pm and ftp is None:
        level_ftp = {"beginner": 150, "intermediate": 220, "advanced": 280, "expert": 340}
        ftp = level_ftp.get("intermediate", 220)

    target_date = None
    if data.get("target_date"):
        target_date = date.fromisoformat(data["target_date"])

    hours = float(data.get("hours_per_week", 7))
    # Infer level from weekly hours
    if hours <= 5:
        level = "beginner"
    elif hours <= 8:
        level = "intermediate"
    elif hours <= 12:
        level = "advanced"
    else:
        level = "expert"

    return AthleteProfileSchema(
        objective=ObjectiveProfile(
            type=data.get("goal", "fitness"),
            target_date=target_date,
        ),
        availability=AvailabilityProfile(
            hours_per_week=hours,
            preferred_days=["tuesday", "thursday", "saturday", "sunday"],
        ),
        level=level,
        structured_plan_history=False,
        equipment=EquipmentProfile(
            power_meter=has_pm,
            ftp=ftp,
            ftp_source=data.get("ftp_source", "estimated"),
        ),
        physio=PhysioProfile(
            age=age,
            hr_max=hr_max,
            hr_max_source=data.get("hr_max_source", "estimated"),
            hr_rest=60,
            hr_rest_source="estimated",
        ),
        coaching_mode="power" if has_pm else "hr",
        health_constraints=False,
        current_ctl=fitness.ctl if fitness else None,
        current_atl=fitness.atl if fitness else None,
        current_tsb=fitness.tsb if fitness else None,
    )


def _build_plan_summary(plan) -> str:
    phase_names = {"base": "Base aérobie", "build": "Construction", "peak": "Pic de forme", "taper": "Affûtage"}
    phase_rows: list[str] = []
    seen: set[str] = set()
    for w in plan.weeks:
        if w.phase in seen:
            continue
        seen.add(w.phase)
        phase_weeks = [x for x in plan.weeks if x.phase == w.phase]
        start_week = phase_weeks[0].week_number
        end_week = phase_weeks[-1].week_number
        avg_tss = sum(x.total_tss_target for x in phase_weeks) / len(phase_weeks)
        phase_rows.append(
            f"{phase_names.get(w.phase, w.phase):<14} | {start_week:>2}-{end_week:<2} | {avg_tss:>3.0f}"
        )

    phases_table = "\n".join([
        "Phase          | Sem.  | TSS moy",
        "---------------|-------|--------",
        *phase_rows,
    ])

    return (
        f"📊 <b>Résumé du plan</b>\n"
        f"• Durée : <b>{plan.weeks_count} semaines</b>\n"
        f"• Charge de départ (TSS) : <b>{plan.initial_weekly_tss:.0f}/semaine</b>\n"
        f"• Charge au pic (TSS) : <b>{plan.peak_weekly_tss:.0f}/semaine</b>\n"
        f"• Mode coaching : <b>{'Puissance (watts)' if plan.coaching_mode == 'power' else 'Fréquence cardiaque'}</b>\n\n"
        f"📅 <b>Phases</b>\n<pre>{phases_table}</pre>"
    )


async def _generate_narrative(user_id, plan, profile, *, bot, chat_id) -> None:
    try:
        from app.llm.narrator import generate_plan_narrative
        from app.db.client import AsyncSessionFactory

        narrative = await generate_plan_narrative(plan, profile.level, profile.objective.type)

        async with AsyncSessionFactory() as new_session:
            async with new_session.begin():
                db_plan = await repo.plan_repo.get_active_plan(new_session, user_id)
                if db_plan:
                    await repo.plan_repo.update_narrative(new_session, db_plan, narrative)

        intro = narrative.get("intro", "")
        if intro:
            await bot.send_message(chat_id=chat_id, text=intro)
    except Exception:
        pass
