"""Setting the athlete up: what the data source knows, plus what only the athlete can say.

Shared by the bot's `/setup` and the agent API. Everything read from the source carries its
origin, and a value the source lacks is asked for rather than silently defaulted (spec 007).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repositories as repo
from app.db.repositories import activity_repo
from app.engine.atl_ctl import compute_fitness_from_any, estimate_initial_ctl
from app.engine.plan_builder import DAY_NAMES
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.engine.tss import tss_from_weekly_hours
from app.providers.intervals.athlete_profile import ReadProfile, read_athlete_profile, stamp
from app.services.fitness import get_current_fitness

logger = logging.getLogger(__name__)

_DEFAULT_AVAILABLE_DAYS = ["tuesday", "thursday", "saturday", "sunday"]
# Plancher réel du moteur (app/engine/plan_builder.py::_build_sessions) — en dessous,
# aucun effet observable sur le plan généré.
_MIN_AVAILABLE_DAYS = 2

_MISSING_PROFILE_FIELDS = {
    "age": ("read_age", "setup.field.age", "setup.unit.years", 35.0, 12.0, 100.0),
    "max_hr": ("read_max_hr", "setup.field.max_hr", "bpm", 185.0, 100.0, 240.0),
    "resting_hr": ("read_resting_hr", "setup.field.resting_hr", "bpm", 60.0, 30.0, 120.0),
}


class SetupError(ValueError):
    def __init__(self, message: str, *, status: int = 422):
        super().__init__(message)
        self.status = status


def level_for_hours(hours: float) -> str:
    if hours <= 5:
        return "beginner"
    if hours <= 8:
        return "intermediate"
    if hours <= 12:
        return "advanced"
    return "expert"


def validate_available_days(days: list[str]) -> list[str]:
    unique = list(dict.fromkeys(days))
    unknown = sorted(set(unique) - set(DAY_NAMES))
    if unknown:
        raise SetupError(f"unknown days {unknown}: use {list(DAY_NAMES)}")
    if len(unique) < _MIN_AVAILABLE_DAYS:
        raise SetupError(f"at least {_MIN_AVAILABLE_DAYS} available days are needed")
    return unique


def read_profile_to_fsm(rp: ReadProfile) -> dict:
    """Flatten a ReadProfile into JSON-safe FSM data. `build_profile` reads this back."""
    return {
        "read_ftp": rp.ftp.value,
        "read_ftp_present": rp.ftp.present,
        "read_lthr": rp.lthr.value,
        "read_max_hr": rp.max_hr.value,
        "read_resting_hr": rp.resting_hr.value,
        "read_weight": rp.weight_kg.value,
        "read_sex": rp.sex.value,
        "read_height_cm": rp.height_cm.value,
        "read_age": rp.age.value,
        "read_has_power_meter": rp.has_power_meter,
        "read_coaching_mode": rp.coaching_mode,
        "read_from_source_at": stamp(),
        "corrections_deferred": {},
    }


def build_profile(data: dict, fitness=None) -> AthleteProfileSchema:
    """Build the profile from the CONFIRMED read values (spec 007 US1) + the five
    answers (goal, date, volume, available days, health constraints). Everything read
    from intervals.icu carries `*_source == "source"` (Constitution IV); nothing is
    silently defaulted.

    FR-007: a value the athlete asked to correct is kept at the source's *current*
    number (the correction must be applied at intervals.icu) — the wanted value lives
    only in `corrections_deferred`, for the recap caveat.
    """
    has_pm = bool(data.get("read_has_power_meter"))
    ftp = data.get("read_ftp")
    supplied = dict(data.get("supplied_profile_values") or {})

    def resolved(field: str, source_key: str, fallback: float) -> tuple[float, str]:
        source_value = data.get(source_key)
        if source_value is not None:
            return float(source_value), "source"
        supplied_value = supplied.get(field)
        if supplied_value is not None:
            return float(supplied_value["value"]), supplied_value["source"]
        # Compatibility only for partial FSM data created before missing-value prompts.
        return fallback, "estimated"

    age, _age_source = resolved("age", "read_age", 35.0)
    max_hr, hr_max_source = resolved("max_hr", "read_max_hr", 220 - age)
    resting_hr, hr_rest_source = resolved("resting_hr", "read_resting_hr", 60.0)
    ftp_source = "source" if ftp is not None else "estimated"
    if has_pm and ftp is None:
        # FR-008-adjacent: only reached if the athlete confirmed a power-meter setup
        # with no FTP anywhere — a coarse level default, flagged estimated.
        ftp = {"beginner": 150, "intermediate": 220, "advanced": 280, "expert": 340}["intermediate"]

    target_date = date.fromisoformat(data["target_date"]) if data.get("target_date") else None

    hours = float(data.get("hours_per_week", 7))

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
        level=level_for_hours(hours),
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
        weight_kg=data.get("read_weight"),
        sex=data.get("read_sex"),
        height_cm=data.get("read_height_cm"),
    )


async def starting_fitness(session, user, hours_per_week: float):
    """`(fitness, seeded)`: from the source if available, else local history, else a documented
    conservative seed, disclosed. `(None, False)` if none of it can be computed."""
    fitness = None
    fitness_is_seeded = False
    try:
        current = await get_current_fitness(session, user.id)
        if current is not None:
            fitness = current.metrics
        else:
            activities = await activity_repo.get_for_user(session, user.id, days=120)
            weekly_tss_seed = tss_from_weekly_hours(hours_per_week)
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
    return fitness, fitness_is_seeded


def _missing_fields(flat: dict) -> list[dict]:
    return [
        {"field": field, "estimate": estimate, "min": low, "max": high}
        for field, (key, _label, _unit, estimate, low, high) in _MISSING_PROFILE_FIELDS.items()
        if flat.get(key) is None
    ]


async def setup_status(session: AsyncSession, client) -> dict:
    """Whether the athlete is set up, and what the source already knows about them."""
    user = await repo.user_repo.get_single_user(session)
    row = await repo.profile_repo.get_by_user_id(session, user.id) if user else None
    try:
        read = await read_athlete_profile(client)
    except Exception as exc:
        logger.exception("read_athlete_profile failed during agent setup")
        raise SetupError("the data source cannot be read right now", status=502) from exc
    flat = read_profile_to_fsm(read)
    return {
        "configured": row is not None,
        "from_source": {
            "ftp": read.ftp.value,
            "lthr": read.lthr.value,
            "max_hr": read.max_hr.value,
            "resting_hr": read.resting_hr.value,
            "age": read.age.value,
            "weight_kg": read.weight_kg.value,
            "sex": read.sex.value,
            "coaching_mode": read.coaching_mode,
        },
        "missing": _missing_fields(flat),
    }


async def complete_setup(
    session: AsyncSession,
    client,
    *,
    hours_per_week: float,
    available_days: list[str],
    health_constraints: bool,
    values: dict[str, float],
    use_estimate: list[str],
    owner_id: int | None,
) -> dict:
    """First-time setup, in freestyle: a plan is a separate choice, with its own approval. Writes
    only Banister's own records, and refuses to touch an athlete already set up."""
    user = await repo.user_repo.get_single_user(session)
    if user and await repo.profile_repo.get_by_user_id(session, user.id):
        raise SetupError("the athlete is already set up: propose a profile change", status=409)
    days = validate_available_days(available_days)

    try:
        read = await read_athlete_profile(client)
    except Exception as exc:
        logger.exception("read_athlete_profile failed during agent setup")
        raise SetupError("the data source cannot be read right now", status=502) from exc
    data = read_profile_to_fsm(read)

    supplied: dict[str, dict] = {}
    unanswered = []
    for item in _missing_fields(data):
        name = item["field"]
        if name in values:
            if not item["min"] <= values[name] <= item["max"]:
                raise SetupError(f"{name} must be between {item['min']} and {item['max']}")
            supplied[name] = {"value": values[name], "source": "declared"}
        elif name in use_estimate:
            supplied[name] = {"value": item["estimate"], "source": "estimated"}
        else:
            unanswered.append(name)
    if unanswered:
        raise SetupError(
            f"the source does not know {unanswered}: ask the athlete, or pass use_estimate "
            "if they do not know"
        )

    data.update(
        supplied_profile_values=supplied,
        hours_per_week=hours_per_week,
        available_days=days,
        health_constraints=health_constraints,
        goal="fitness",
    )
    if user is None:
        user = await repo.user_repo.create_single_user(
            session, telegram_id=settings.telegram_owner_id or owner_id or 0
        )
    fitness, seeded = await starting_fitness(session, user, hours_per_week)
    profile = build_profile(data, fitness)
    stored = profile.model_dump(mode="json")
    stored["read_from_source_at"] = data.get("read_from_source_at")
    await repo.profile_repo.create(session, user.id, stored)
    user.onboarding_completed_at = datetime.now(UTC)

    result = {
        "configured": True,
        "coaching_mode": profile.coaching_mode,
        "fitness_is_seed": seeded,
        "history": "the ride history is being imported from the source: a few minutes",
    }
    if user.disclaimer_acknowledged_at is None:
        from app.llm.prompts import disclaimer_text

        result["card"] = {"text": disclaimer_text(), "parse_mode": "HTML"}
        await repo.user_repo.ack_disclaimer(session, user)
    return result


async def update_profile(
    session: AsyncSession,
    user,
    *,
    hours_per_week: float | None,
    available_days: list[str] | None,
    health_constraints: bool | None,
) -> dict:
    """Volume, days and constraints: what only the athlete knows, said in conversation. Applied
    directly: nothing here reaches the calendar or an active plan. Thresholds stay at the source."""
    row = await repo.profile_repo.get_by_user_id(session, user.id)
    if row is None or not row.profile:
        raise SetupError("the athlete has no profile yet", status=409)
    data = dict(row.profile)
    availability = dict(data.get("availability") or {})
    changed: dict = {}
    if hours_per_week is not None and hours_per_week != availability.get("hours_per_week"):
        availability["hours_per_week"] = hours_per_week
        data["level"] = level_for_hours(hours_per_week)
        changed["hours_per_week"] = hours_per_week
    if available_days is not None:
        days = validate_available_days(available_days)
        if set(days) != set(availability.get("preferred_days") or []):
            availability["preferred_days"] = days
            changed["available_days"] = days
    if health_constraints is not None and health_constraints != data.get("health_constraints"):
        data["health_constraints"] = health_constraints
        changed["health_constraints"] = health_constraints
    if not changed:
        return {"changed": {}, "active_plan_unchanged": False}
    data["availability"] = availability
    await repo.profile_repo.update(session, row, data)
    plan = await repo.plan_repo.get_active_plan(session, user.id)
    return {"changed": changed, "active_plan_unchanged": plan is not None}
