"""
Commande /setup — configuration initiale et régénération du plan (spec 007).

Flow FSM (SetupStates) :
  lecture de la source → CONFIRM_PROFILE → [CORRECT_VALUE] → GOAL → DATE → VOLUME
  → AVAILABLE_DAYS → CONSTRAINTS → génération du plan

Setup lit tout ce qu'intervals.icu sait de l'athlète (FTP, FC, poids, âge, forme,
volume réel), le fait confirmer, puis ne demande que ce qui ne peut pas être lu :
l'objectif, sa date, le volume voulu, les jours disponibles, les contraintes santé.

AVAILABLE_DAYS (ajouté 2026-09-18) : avant ça, `_build_profile` hardcodait
["tuesday", "thursday", "saturday", "sunday"] pour tout le monde, sans jamais demander —
`app/engine/plan_builder.py` a un vrai fallback pour une liste vide, mais elle n'était
jamais vide en pratique. Pré-sélectionne ces 4 jours comme point de départ (même
philosophie que CONFIRM_PROFILE : montrer une valeur plausible et la faire confirmer/
corriger plutôt que poser une question à froid), athlète libre de tout changer avant de
valider. Minimum 2 jours — c'est le plancher réel du moteur (`_build_sessions` :
`n_sessions = max(2, min(...))`), en dessous ça n'a aucun effet observable.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.states import PlanStates, SetupStates
from app.config import settings
from app.db import repositories as repo
from app.db.repositories import activity_repo
from app.engine.atl_ctl import compute_fitness_from_any, estimate_initial_ctl
from app.engine.plan_builder import DAY_NAMES, generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.engine.tss import tss_from_weekly_hours
from app.llm.tools import DAY_NAMES_FR
from app.providers.intervals.athlete_profile import ReadProfile, read_athlete_profile, stamp
from app.providers.intervals.client import IntervalsClient
from app.services.fitness import get_current_fitness

# Point de départ pré-sélectionné pour AVAILABLE_DAYS — même 4 jours que l'ancien défaut
# hardcodé, mais maintenant modifiable par l'athlète avant validation, jamais silencieux.
_DEFAULT_AVAILABLE_DAYS = ["tuesday", "thursday", "saturday", "sunday"]
# Plancher réel du moteur (app/engine/plan_builder.py::_build_sessions) — en dessous,
# aucun effet observable sur le plan généré.
_MIN_AVAILABLE_DAYS = 2

logger = logging.getLogger(__name__)
router = Router()


def _intervals_client() -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        athlete_id=settings.intervals_athlete_id,
    )

# ── Keyboards ──────────────────────────────────────────────────────────────────

def _kb(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in buttons]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("✅ Tout est bon", "setup:confirm:ok"),
        ("✏️ Corriger une valeur", "setup:confirm:edit"),
    ])


def correct_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("FTP", "setup:correct:ftp"),
        ("FC max", "setup:correct:max_hr"),
        ("FC repos", "setup:correct:resting_hr"),
        ("Poids", "setup:correct:weight"),
        ("↩️ Revenir", "setup:correct:back"),
    ])


def constraints_keyboard() -> InlineKeyboardMarkup:
    return _kb([
        ("Aucune contrainte santé", "setup:constraints:none"),
        ("J'en ai une — je la décris", "setup:constraints:describe"),
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


def available_days_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Grille de jours à cocher/décocher (toggle), + un bouton de validation qui
    affiche le compte courant pour rendre le minimum de 2 visible sans texte d'erreur
    à part si l'athlète essaie de valider en dessous."""
    day_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if en in selected else ''}{fr}",
            callback_data=f"setup:day:{en}",
        )
        for en, fr in zip(DAY_NAMES, DAY_NAMES_FR)
    ]
    rows = [day_buttons[i:i + 2] for i in range(0, len(day_buttons), 2)]
    rows.append([
        InlineKeyboardButton(
            text=f"➡️ Valider ({len(selected)} jour{'s' if len(selected) > 1 else ''})",
            callback_data="setup:days:confirm",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_AVAILABLE_DAYS_TEXT = (
    "\U0001f4c6 Sur quels jours peux-tu t'entraîner ? Coche/décoche, puis valide "
    "(2 minimum).\n\n"
    "Point de départ suggéré ci-dessous — change-le si ça ne colle pas à ton emploi "
    "du temps."
)


# ── Entry point : read the source, then confirm ───────────────────────────────

_NO_DATE = ("aucune", "none", "-", "skip")


def parse_goal_date(text: str) -> tuple[date | None, str | None]:
    """Shared by /setup and /goal (FR-015). Returns (date, problem):
    problem ∈ None | "format" | "past" | "too_soon" | "too_far".
    "too_soon" / "too_far" still yield the date — the caller decides whether to warn or
    require a re-confirm."""
    raw = text.strip().lower()
    if raw in _NO_DATE:
        return None, None
    try:
        d = date.fromisoformat(text.strip())
    except ValueError:
        return None, "format"
    if d <= date.today():
        return None, "past"
    days = (d - date.today()).days
    if days < 21:
        return d, "too_soon"
    if days > 365:
        return d, "too_far"
    return d, None


def _read_profile_to_fsm(rp: ReadProfile) -> dict:
    """Flatten a ReadProfile into JSON-safe FSM data. `_build_profile` reads this back."""
    return {
        "read_ftp": rp.ftp.value,
        "read_ftp_present": rp.ftp.present,
        "read_lthr": rp.lthr.value,
        "read_max_hr": rp.max_hr.value,
        "read_resting_hr": rp.resting_hr.value,
        "read_weight": rp.weight_kg.value,
        "read_sex": rp.sex.value,
        "read_age": rp.age.value,
        "read_has_power_meter": rp.has_power_meter,
        "read_coaching_mode": rp.coaching_mode,
        "read_from_source_at": stamp(),
        "corrections_deferred": {},
    }


def _render_confirm_screen(rp: ReadProfile) -> str:
    def row(label: str, rv, unit: str) -> str:
        if not rv.present:
            return f"  {label:<16} — absent, je te le demanderai"
        note = f"\n  {'':<16}⚠️ {rv.note}" if rv.note else ""
        return f"  {label:<16} <b>{rv.value}</b> {unit}   · {rv.origin}{note}"

    mode = "puissance" if rp.coaching_mode == "power" else "fréquence cardiaque"
    age_bit = f"· {rp.age.value} ans" if rp.age.present else ""
    lines = [
        "\U0001f4cb <b>Voici ce que je sais déjà de toi</b> (lu depuis intervals.icu) :",
        "",
        row("FTP", rp.ftp, "W"),
        row("LTHR", rp.lthr, "bpm"),
        row("FC max", rp.max_hr, "bpm"),
        row("FC repos", rp.resting_hr, "bpm"),
        row("Poids", rp.weight_kg, "kg"),
        row("Sexe / âge", rp.sex, age_bit),
        "",
        f"Pilotage : <b>{mode}</b>.",
        "",
        "C'est bon ? Sinon corrige — mais ces valeurs vivent dans ton compte "
        "intervals.icu, je lis depuis là.",
    ]
    return "\n".join(lines)


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext, session: AsyncSession, user) -> None:
    await state.clear()
    await message.answer("⏳ Je lis ton profil intervals.icu…")
    try:
        rp = await read_athlete_profile(_intervals_client())
    except Exception:
        logger.exception("read_athlete_profile failed during /setup")
        await message.answer(
            "⚠️ Je n'arrive pas à lire ton profil intervals.icu pour l'instant. "
            "Vérifie ta clé API et réessaie /setup dans un moment."
        )
        return

    await state.update_data(**_read_profile_to_fsm(rp))
    await state.set_state(SetupStates.CONFIRM_PROFILE)
    await message.answer(
        _render_confirm_screen(rp), parse_mode="HTML", reply_markup=confirm_keyboard()
    )


# ── CONFIRM_PROFILE ──────────────────────────────────────────────────────────

@router.callback_query(SetupStates.CONFIRM_PROFILE, F.data == "setup:confirm:ok")
async def confirm_ok(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SetupStates.GOAL)
    await callback.message.edit_text(
        "\U0001f44d Parfait. Maintenant ce que je ne peux pas deviner.\n\n"
        "Quel est ton objectif ?",
        reply_markup=goal_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(SetupStates.CONFIRM_PROFILE, F.data == "setup:confirm:edit")
async def confirm_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SetupStates.CORRECT_VALUE)
    await callback.message.edit_text(
        "Quelle valeur veux-tu corriger ?", reply_markup=correct_keyboard()
    )
    await callback.answer()


# ── CORRECT_VALUE ────────────────────────────────────────────────────────────
# Phase 4 (US2) adds the write-to-source branch. Here: capture the new value, keep
# the source's current one (FR-007 default), note the caveat for the recap.

_CORRECT_LABELS = {
    "ftp": ("FTP", "W"), "max_hr": ("FC max", "bpm"),
    "resting_hr": ("FC repos", "bpm"), "weight": ("Poids", "kg"),
}
_CORRECT_SRC_KEY = {
    "ftp": "read_ftp", "max_hr": "read_max_hr",
    "resting_hr": "read_resting_hr", "weight": "read_weight",
}


@router.callback_query(SetupStates.CORRECT_VALUE, F.data == "setup:correct:back")
async def correct_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SetupStates.CONFIRM_PROFILE)
    await callback.message.edit_text("Reprenons. C'est bon ?", reply_markup=confirm_keyboard())
    await callback.answer()


@router.callback_query(SetupStates.CORRECT_VALUE, F.data.startswith("setup:correct:"))
async def correct_pick(callback: CallbackQuery, state: FSMContext) -> None:
    field = callback.data.split(":")[2]
    if field not in _CORRECT_LABELS:
        await callback.answer()
        return
    label, unit = _CORRECT_LABELS[field]
    await state.update_data(_correcting=field)
    await callback.message.edit_text(
        f"Nouvelle valeur pour <b>{label}</b> (en {unit}) ? Envoie juste le nombre.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SetupStates.CORRECT_VALUE, F.text)
async def correct_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data.get("_correcting")
    if not field:
        await message.answer("Choisis d'abord une valeur à corriger.")
        return
    label, unit = _CORRECT_LABELS[field]
    try:
        new_val = float(message.text.strip().replace(",", "."))
    except ValueError:
        await message.answer(f"⚠️ Envoie un nombre pour {label}.")
        return

    current = data.get(_CORRECT_SRC_KEY[field])
    deferred = dict(data.get("corrections_deferred") or {})
    deferred[field] = {"wanted": new_val, "current": current}
    await state.update_data(corrections_deferred=deferred, _correcting=None)
    await state.set_state(SetupStates.CONFIRM_PROFILE)
    await message.answer(
        f"Noté : tu veux {label} à {new_val:g} {unit} (actuellement {current} {unit} "
        f"selon intervals.icu).\n\n"
        f"Cette valeur vit dans ton compte intervals.icu — change-la là "
        f"(Réglages → Sport → {label}), je relirai. En attendant je construis ton "
        f"plan sur {current} {unit}.",
        reply_markup=confirm_keyboard(),
    )


# ── GOAL ─────────────────────────────────────────────────────────────────────

@router.callback_query(SetupStates.GOAL, F.data.startswith("setup:goal:"))
async def setup_goal(callback: CallbackQuery, state: FSMContext) -> None:
    goal = callback.data.split(":")[2]
    await state.update_data(goal=goal)
    await state.set_state(SetupStates.DATE)
    await callback.message.edit_text(
        "\U0001f4c5 As-tu un événement cible ?\n\n"
        "Réponds avec la date au format <code>AAAA-MM-JJ</code> "
        "ou tape <code>aucune</code> si tu n'as pas d'échéance.",
        parse_mode="HTML",
    )
    await callback.answer()


# ── DATE ─────────────────────────────────────────────────────────────────────

@router.message(SetupStates.DATE)
async def setup_date(message: Message, state: FSMContext) -> None:
    target_date, problem = parse_goal_date(message.text)
    if problem == "format":
        await message.answer(
            "⚠️ Format non reconnu. Utilise <code>AAAA-MM-JJ</code> ou tape <code>aucune</code>.",
            parse_mode="HTML",
        )
        return
    if problem == "past":
        await message.answer(
            "⚠️ La date doit être dans le futur. Réessaie ou tape <code>aucune</code>.",
            parse_mode="HTML",
        )
        return
    if problem == "too_soon":
        data = await state.get_data()
        if data.get("_date_confirmed") != target_date.isoformat():
            await state.update_data(_date_confirmed=target_date.isoformat())
            days = (target_date - date.today()).days
            await message.answer(
                f"⚠️ {days} jours, c'est très court pour un vrai bloc. "
                "Renvoie la même date pour confirmer, ou choisis-en une plus lointaine."
            )
            return
    if problem == "too_far":
        await message.answer(
            "ℹ️ Si loin, le plan est surtout de la spéculation — "
            "je le construis quand même, mais vise plutôt un point plus proche."
        )

    await state.update_data(
        target_date=target_date.isoformat() if target_date else None, _date_confirmed=None
    )
    await state.set_state(SetupStates.VOLUME)
    recent = (await state.get_data()).get("read_recent_hours")
    hint = f" (tes 6 dernières semaines : ~{recent:.0f} h/sem)" if recent else ""
    await message.answer(
        f"\U0001f550 Combien d'heures par semaine tu <b>veux</b> t'entraîner ?{hint}",
        parse_mode="HTML",
        reply_markup=volume_keyboard(),
    )


# ── VOLUME ───────────────────────────────────────────────────────────────────

@router.callback_query(SetupStates.VOLUME, F.data.startswith("setup:vol:"))
async def setup_volume(callback: CallbackQuery, state: FSMContext) -> None:
    hours = float(callback.data.split(":")[2])
    selected = list(_DEFAULT_AVAILABLE_DAYS)
    await state.update_data(hours_per_week=hours, available_days=selected)
    await state.set_state(SetupStates.AVAILABLE_DAYS)
    await callback.message.edit_text(
        _AVAILABLE_DAYS_TEXT,
        parse_mode="HTML",
        reply_markup=available_days_keyboard(selected),
    )
    await callback.answer()


# ── AVAILABLE_DAYS ───────────────────────────────────────────────────────────

@router.callback_query(SetupStates.AVAILABLE_DAYS, F.data.startswith("setup:day:"))
async def setup_days_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    day = callback.data.split(":")[2]
    if day not in DAY_NAMES:
        await callback.answer()
        return
    selected = list((await state.get_data()).get("available_days") or [])
    if day in selected:
        selected.remove(day)
    else:
        selected.append(day)
    await state.update_data(available_days=selected)
    await callback.message.edit_reply_markup(reply_markup=available_days_keyboard(selected))
    await callback.answer()


@router.callback_query(SetupStates.AVAILABLE_DAYS, F.data == "setup:days:confirm")
async def setup_days_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    selected = list((await state.get_data()).get("available_days") or [])
    if len(selected) < _MIN_AVAILABLE_DAYS:
        await callback.answer(
            f"Choisis au moins {_MIN_AVAILABLE_DAYS} jours.", show_alert=True
        )
        return
    await state.set_state(SetupStates.CONSTRAINTS)
    await callback.message.edit_text(
        "\U0001fa7a Dernière question — aucune source ne la connaît : as-tu une "
        "contrainte santé qui limite ce que tu peux faire en sécurité ?",
        reply_markup=constraints_keyboard(),
    )
    await callback.answer()


# ── CONSTRAINTS ──────────────────────────────────────────────────────────────

@router.callback_query(SetupStates.CONSTRAINTS, F.data == "setup:constraints:none")
async def constraints_none(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, user
) -> None:
    await state.update_data(health_constraints=False)
    await callback.message.edit_text("⏳ Génération de ton plan en cours…")
    await callback.answer()
    await _finalize_setup(callback.message, state, session, user, await state.get_data())


@router.callback_query(SetupStates.CONSTRAINTS, F.data == "setup:constraints:describe")
async def constraints_describe(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Décris-la en une phrase (zone du corps, sévérité, ce que tu évites)."
    )
    await callback.answer()


@router.message(SetupStates.CONSTRAINTS, F.text)
async def constraints_text(
    message: Message, state: FSMContext, session: AsyncSession, user
) -> None:
    await state.update_data(
        health_constraints=True, health_constraints_note=message.text.strip()[:500]
    )
    await message.answer("⏳ Génération de ton plan en cours…")
    await _finalize_setup(message, state, session, user, await state.get_data())


# ── Finalization : create user, build profile, generate plan ───────────────────

async def _finalize_setup(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user,
    data: dict,
) -> None:
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
    fitness_is_seeded = False
    try:
        current = await get_current_fitness(session, user.id)
        if current is not None:
            fitness = current.metrics
        else:
            activities = await activity_repo.get_for_user(session, user.id, days=120)
            weekly_tss_seed = tss_from_weekly_hours(float(data.get("hours_per_week", 5)))
            initial_ctl_seed = estimate_initial_ctl(weekly_tss_seed)
            seed_date = date.today() - timedelta(days=120)
            if activities and len(activities) >= 4:
                fitness = compute_fitness_from_any(
                    activities, initial_ctl=initial_ctl_seed, seed_date=seed_date
                )
            else:
                # FR-010 — too little history to measure current fitness: use a
                # documented conservative seed and disclose it.
                fitness = compute_fitness_from_any(
                    activities or [], initial_ctl=initial_ctl_seed, seed_date=seed_date
                )
                fitness_is_seeded = True
    except Exception:
        pass

    profile = _build_profile(data, fitness)
    plan = generate_plan(profile)

    plan_dict = plan.model_dump(mode="json")
    start_date = plan.start_date or date.today()
    end_date = plan.end_date or date.today()

    # Deactivate old plans, then create new one
    await repo.plan_repo.deactivate_all_for_user(session, user.id)
    await repo.plan_repo.create(
        session=session,
        user_id=user.id,
        plan_technical=plan_dict,
        start_date=start_date,
        end_date=end_date,
    )

    # Save/overwrite athlete profile — with the source-read provenance markers (FR-005).
    profile_data = profile.model_dump(mode="json")
    profile_data["read_from_source_at"] = data.get("read_from_source_at")
    if data.get("corrections_deferred"):
        profile_data["corrections_deferred"] = data["corrections_deferred"]
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
    built_from = _built_from_recap(profile, data, fitness_is_seeded)
    await message.answer(
        f"🎉 <b>Ton plan est prêt !</b>\n\n{summary}\n\n{built_from}\n\n"
        "Tape /plan pour voir ta première semaine en détail. 🚴",
        parse_mode="HTML",
    )

    # Disclaimer avant la première interaction de coaching — une seule fois (FR-028).
    if user.disclaimer_acknowledged_at is None:
        from app.llm.prompts import DISCLAIMER_TEXT

        await message.answer(DISCLAIMER_TEXT, parse_mode="HTML")
        await repo.user_repo.ack_disclaimer(session, user)

    # LLM narrative in background (non-blocking)
    import asyncio
    asyncio.create_task(_generate_narrative(
        user.id, plan, profile,
        bot=message.bot,
        chat_id=message.chat.id,
    ))


def _build_profile(data: dict, fitness=None) -> AthleteProfileSchema:
    """Build the profile from the CONFIRMED read values (spec 007 US1) + the five
    answers (goal, date, volume, available days, health constraints). Everything read
    from intervals.icu carries `*_source == "source"` (Constitution IV); nothing is
    silently defaulted.

    FR-007: a value the athlete asked to correct is kept at the source's *current*
    number (the correction must be applied at intervals.icu) — the wanted value lives
    only in `corrections_deferred`, for the recap caveat.
    """
    has_pm = bool(data.get("read_has_power_meter"))
    ftp = data.get("read_ftp")                    # source value, kept even if "corrected"
    max_hr = data.get("read_max_hr")
    resting_hr = data.get("read_resting_hr")
    age = data.get("read_age") or 35

    ftp_source = "source" if ftp is not None else "estimated"
    hr_max_source = "source" if max_hr is not None else "estimated"
    hr_rest_source = "source" if resting_hr is not None else "estimated"

    if max_hr is None:
        max_hr = 220 - int(age)
    if resting_hr is None:
        resting_hr = 60
    if has_pm and ftp is None:
        # FR-008-adjacent: only reached if the athlete confirmed a power-meter setup
        # with no FTP anywhere — a coarse level default, flagged estimated.
        ftp = {"beginner": 150, "intermediate": 220, "advanced": 280, "expert": 340}["intermediate"]

    target_date = date.fromisoformat(data["target_date"]) if data.get("target_date") else None

    hours = float(data.get("hours_per_week", 7))
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
            # AVAILABLE_DAYS always sets this in the live flow; the default here is
            # only a safety net for state data missing the key (e.g. an old/partial
            # FSM state from before this question existed), not the normal path.
            preferred_days=list(data.get("available_days") or _DEFAULT_AVAILABLE_DAYS),
        ),
        level=level,
        structured_plan_history=False,
        equipment=EquipmentProfile(
            power_meter=has_pm,
            ftp=int(ftp) if ftp is not None else None,
            ftp_source=ftp_source,
        ),
        physio=PhysioProfile(
            age=int(age),
            hr_max=int(max_hr),
            hr_max_source=hr_max_source,
            hr_rest=int(resting_hr),
            hr_rest_source=hr_rest_source,
        ),
        coaching_mode=data.get("read_coaching_mode") or ("power" if has_pm else "hr"),
        health_constraints=bool(data.get("health_constraints")),
        current_ctl=fitness.ctl if fitness else None,
        current_atl=fitness.atl if fitness else None,
        current_tsb=fitness.tsb if fitness else None,
    )


def _built_from_recap(profile: AthleteProfileSchema, data: dict, seeded: bool) -> str:
    """FR-004 — every input the plan was built from, visible with its origin."""
    e, p, o = profile.equipment, profile.physio, profile.objective
    src = {"source": "lu depuis intervals.icu", "declared": "que tu as donné",
           "estimated": "estimé"}
    lines = ["🧾 <b>Ce sur quoi j'ai construit ton plan</b>"]
    if e.ftp:
        lines.append(f"  • FTP {e.ftp} W · {src.get(e.ftp_source, e.ftp_source)}")
    lines.append(f"  • FC max {p.hr_max} bpm · {src.get(p.hr_max_source, p.hr_max_source)}")
    lines.append(
        f"  • FC repos {p.hr_rest} bpm · "
        f"{src.get(p.hr_rest_source, p.hr_rest_source)}"
    )
    when = f" le {o.target_date:%d/%m/%Y}" if o.target_date else ""
    lines.append(f"  • Objectif : {o.type}{when}")
    hrs = profile.availability.hours_per_week
    lines.append(f"  • Volume voulu : {hrs:g} h/sem (ton choix)")
    chosen_days = profile.availability.preferred_days
    day_indices = sorted(DAY_NAMES.index(d) for d in chosen_days if d in DAY_NAMES)
    days_fr = [DAY_NAMES_FR[i] for i in day_indices]
    if days_fr:
        lines.append(f"  • Jours disponibles : {', '.join(days_fr)} (ton choix)")
    if profile.health_constraints:
        lines.append("  • Contrainte santé prise en compte")
    if seeded:
        lines.append(
            "  • Forme de départ : <i>estimation prudente</i> — pas assez d'historique "
            "pour la mesurer, elle se calera sur tes données réelles en quelques semaines"
        )
    for field, info in (data.get("corrections_deferred") or {}).items():
        lines.append(
            f"  ⚠️ {field} : tu voulais {info['wanted']:g}, je garde {info['current']} "
            "tant qu'intervals.icu n'est pas à jour"
        )
    return "\n".join(lines)


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
        from app.db.client import AsyncSessionFactory
        from app.llm.narrator import generate_plan_narrative

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
