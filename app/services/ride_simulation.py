"""Simulate a ride before doing it: time and route description, effort, and form after it.

Nothing is written. The physics (time, power, climbs, TSS, kJ) comes from the open-source
`allure-cycling` library (https://github.com/gwenaelkreutner/Allure); Banister only gathers
the inputs (route, FTP and weight read fresh from the data source) and, when a date is
given, projects CTL/ATL/TSB with its own engine (`app/engine/ride_simulation.py`) and runs
the same workload guardrails as everywhere else. Fuelling is deliberately absent: Banister
owns no nutrition logic; duration and kJ are what a nutrition service needs.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from allure_cycling import (
    Environment,
    Group,
    Mode,
    PowerTarget,
    Race,
    Rider,
    Route,
    RouteSourceError,
    Settings,
    Solo,
    estimate,
    load_gpx,
    load_url,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import paris_today
from app.db import repositories as repo
from app.engine.guardrails import evaluate_acwr, evaluate_ramp_rate
from app.engine.ride_simulation import FormPoint, project_ride
from app.engine.schemas import AthleteProfileSchema, TrainingPlanSchema
from app.providers.intervals.athlete_profile import read_athlete_profile
from app.providers.intervals.calendar import iter_horizon_sessions
from app.providers.intervals.client import IntervalsClient
from app.services.fitness import get_current_fitness

DEFAULT_GROUP_SIZE = {"group": 15, "race": 80}
MAX_DAYS_AHEAD = 60


class RideSimulationError(ValueError):
    """Explained to the athlete as is: the route or an input is missing or unusable."""


@dataclass(frozen=True)
class RideRequest:
    url: str | None = None
    gpx: str | None = None
    track_ride_date: date | None = None  # reuse the track of a past ride
    date: date | None = None  # when the athlete plans to ride: adds the form projection
    mode: str | None = None
    watts: float | None = None
    watts_kind: str = "np"
    context: str = "solo"
    group_size: int | None = None
    temperature_c: float | None = None


def _hms(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(round(seconds / 60.0)) * 60
    return f"{s // 3600}h{s % 3600 // 60:02d}"


def _r(x, digits: int = 1):
    return None if x is None else round(float(x), digits)


async def _ride_route(client: IntervalsClient, day: date) -> tuple[Route, str]:
    rides = [
        a for a in await client.list_activities(oldest=day.isoformat(), newest=day.isoformat())
        if a.get("type") in ("Ride", "GravelRide") and not a.get("trainer")
    ]
    if not rides:
        raise RideSimulationError(f"no outdoor ride on {day.isoformat()}")
    ride = max(rides, key=lambda a: a.get("distance") or 0)
    raw = await client.get_activity_streams(ride["id"], types=["latlng", "altitude"])
    by_type = {s.get("type"): s for s in raw}
    latlng, alt = by_type.get("latlng"), by_type.get("altitude")
    if not latlng or not alt or not latlng.get("data2"):
        raise RideSimulationError("that ride has no GPS track with elevation")
    pts = [
        (a, b, c) for a, b, c in zip(latlng["data"], latlng["data2"], alt["data"])
        if None not in (a, b, c)
    ]
    if len(pts) < 2:
        raise RideSimulationError("that ride has no GPS track with elevation")
    la, lo, el = zip(*pts)
    label = f"ride of {day.isoformat()} ({ride.get('name') or ride['id']})"
    return Route.from_points(la, lo, el), label


async def _rider(
    client: IntervalsClient, stored: AthleteProfileSchema | None
) -> tuple[float | None, float | None]:
    ftp = weight = None
    try:
        read = await read_athlete_profile(client)
        ftp, weight = read.ftp.value, read.weight_kg.value
    except Exception:  # the stored profile is the fallback
        pass
    if stored is not None:
        ftp = ftp or stored.equipment.ftp
        weight = weight or stored.weight_kg
    return (float(ftp) if ftp else None), (float(weight) if weight else None)


def _context(req: RideRequest):
    if req.context == "solo":
        return Solo()
    size = req.group_size or DEFAULT_GROUP_SIZE[req.context]
    return Race(size=size) if req.context == "race" else Group(size=size)


def _point(p: FormPoint) -> dict:
    return {"date": p.date.isoformat(), "ctl": _r(p.ctl), "atl": _r(p.atl), "tsb": _r(p.tsb)}


async def _form(
    session: AsyncSession, user_id: uuid.UUID, ride_date: date, ride_tss: float | None
) -> dict:
    if ride_tss is None:
        return {"available": False, "reason": "no FTP: the ride's load cannot be computed"}
    today = paris_today()
    fitness = await get_current_fitness(session, user_id, today=today)
    if fitness is None:
        return {"available": False, "reason": "no form data from the source yet"}
    as_of = fitness.as_of
    if ride_date <= as_of:
        return {"available": False, "reason": "the form for that day is already measured"}

    counted, not_counted, replaced = [], [], None
    plan = await repo.plan_repo.get_active_plan(session, user_id)
    planned: dict[date, float] = {}
    if plan is not None:
        schema = TrainingPlanSchema.model_validate(plan.plan_technical)
        for p in iter_horizon_sessions(schema, as_of + timedelta(days=1), ride_date, today=today):
            if p.session_date == ride_date:
                replaced = {"date": p.session_date.isoformat(), "workout_type": p.spec.workout_type,
                            "tss_target": _r(p.spec.tss_target, 0)}
                continue
            planned[p.session_date] = planned.get(p.session_date, 0.0) + p.spec.tss_target
            counted.append({"date": p.session_date.isoformat(), "workout_type": p.spec.workout_type,
                            "tss": _r(p.spec.tss_target, 0)})
    for entry in await repo.freestyle_publication_repo.get_active_for_user(session, user_id):
        if as_of < entry.session_date < ride_date:
            not_counted.append({"date": entry.session_date.isoformat(),
                                "workout_type": entry.workout_type})

    history = await repo.wellness_repo.get_range(
        session, user_id, as_of - timedelta(days=14), as_of
    )
    proj = project_ride(
        ctl=fitness.metrics.ctl, atl=fitness.metrics.atl, as_of=as_of, ride_date=ride_date,
        ride_tss=ride_tss, planned=planned,
        history_ctl={w.date: w.ctl for w in history if w.ctl is not None},
    )
    ramp = proj.after.ctl - proj.ctl_week_before if proj.ctl_week_before is not None else None
    findings = [
        f for f in (
            evaluate_acwr(proj.after.atl, proj.after.ctl, finding_date=ride_date),
            evaluate_ramp_rate(ramp, finding_date=ride_date),
        ) if f is not None
    ]
    return {
        "available": True,
        "from_source": {"date": as_of.isoformat(), "ctl": _r(fitness.metrics.ctl),
                        "atl": _r(fitness.metrics.atl), "tsb": _r(fitness.metrics.tsb)},
        "day_before_ride": _point(proj.day_before),
        "after_ride": _point(proj.after),
        "ramp_7d": _r(ramp),
        "findings": [
            {"kind": f.kind, "observed": f.observed, "reference": f.reference,
             "action": f.action, "severity": f.severity}
            for f in findings
        ],
        "planned_sessions_counted": counted,
        "freestyle_sessions_not_counted": not_counted,
        "replaces_planned_session": replaced,
        "assumption": "days without a planned session count as rest; values are end of day",
    }


async def simulate_ride(
    req: RideRequest,
    client: IntervalsClient,
    session: AsyncSession,
    user_id: uuid.UUID,
    stored: AthleteProfileSchema | None,
) -> dict:
    if sum(x is not None for x in (req.url, req.gpx, req.track_ride_date)) != 1:
        raise RideSimulationError("give exactly one route: url, gpx or track_ride_date")
    if (req.mode is None) == (req.watts is None):
        raise RideSimulationError("give exactly one of: mode, watts")
    if req.context not in ("solo", "group", "race"):
        raise RideSimulationError("context must be solo, group or race")
    if req.date is not None and not (
        paris_today() <= req.date <= paris_today() + timedelta(days=MAX_DAYS_AHEAD)
    ):
        raise RideSimulationError(f"date must be between today and {MAX_DAYS_AHEAD} days ahead")

    try:
        if req.url:
            route, source = await asyncio.to_thread(load_url, req.url), req.url
        elif req.gpx:
            route, source = load_gpx(io.StringIO(req.gpx)), "GPX file"
        else:
            route, source = await _ride_route(client, req.track_ride_date)
    except RouteSourceError as exc:
        raise RideSimulationError(str(exc)) from None
    except ValueError as exc:  # unreadable GPX, no elevation…
        raise RideSimulationError(f"unusable route: {exc}") from None

    ftp, weight = await _rider(client, stored)
    if weight is None:
        raise RideSimulationError("the athlete's weight is unknown: set it on intervals.icu")
    if req.mode is not None and ftp is None:
        raise RideSimulationError(
            "an intensity mode needs the FTP: set it on intervals.icu, or give watts"
        )
    try:
        mode = Mode(req.mode) if req.mode is not None else None
    except ValueError:
        raise RideSimulationError("mode must be chill, steady, tempo or race") from None

    env = Environment()
    if req.temperature_c is not None:
        env = Environment(temperature_c=req.temperature_c)
    power = PowerTarget(req.watts, req.watts_kind) if req.watts is not None else None
    ctx = _context(req)
    res = await asyncio.to_thread(
        estimate, route, Rider(weight, ftp), mode=mode, power=power, context=ctx,
        settings=Settings(environment=env),
    )

    notes = []
    if req.temperature_c is None:
        notes.append("temperature assumed 15 °C; cold air is denser (about 1.5 % slower at 0 °C)")
    if req.context == "group":
        notes.append("group: drafting with relays and regroupings after climbs; "
                     "group constants rest on few rides")
    if req.context == "race":
        notes.append("race: closed roads, sheltered in the bunch; calibrated on two races only")

    out = {
        "route": {
            "source": source,
            "distance_km": _r(res.distance_km),
            "elevation_gain_m": round(res.elevation_gain_m),
            "elevation_loss_m": round(res.elevation_loss_m),
            "climbs": [
                {"start_km": c.start_km, "length_km": c.length_km, "gain_m": c.gain_m,
                 "avg_grade_pct": _r(c.avg_grade * 100), "max_grade_pct": _r(c.max_grade * 100),
                 "hardest_km_pct": _r(c.steepest_km_grade * 100),
                 "category": c.category, "time": _hms(c.time_s), "speed_kmh": _r(c.speed_kmh),
                 "avg_power_w": _r(c.avg_power_w, 0)}
                for c in res.climbs
            ],
            "descents": [
                {"start_km": d.start_km, "length_km": d.length_km, "loss_m": d.loss_m,
                 "avg_grade_pct": _r(d.avg_grade * 100), "time": _hms(d.time_s)}
                for d in res.descents
            ],
            "climb_category_rule": "Strava: length m x grade %; Cat 4 > 8000 ... HC > 80000",
        },
        "estimate": {
            "moving_time": _hms(res.moving_time_s),
            "moving_time_s": round(res.moving_time_s),
            "avg_speed_kmh": _r(res.avg_speed_kmh),
            "regroupings_min": round(res.regroup_s / 60),
            "checkpoints": [
                {"km": cp.km, "elevation_m": cp.elevation_m, "elapsed": _hms(cp.elapsed_s)}
                for cp in res.checkpoints
            ],
        },
        "effort": {
            "mode": mode.value if mode else None,
            "normalized_power_w": round(res.normalized_power_w),
            "avg_power_w": round(res.avg_power_w),
            "intensity_factor": _r(res.intensity_factor, 2),
            "tss": _r(res.tss, 0),
            "kilojoules": round(res.kilojoules),
        },
        "inputs": {
            "ftp_w": ftp, "weight_kg": weight, "context": req.context,
            "group_size": getattr(ctx, "size", None), "temperature_c": env.temperature_c,
        },
        "notes": notes,
        "method": "allure-cycling: Martin power model, terrain pacing, drafting; moving time "
                  "only, stops excluded; TSS = hours x IF^2 x 100; nothing is saved",
    }
    if req.date is not None:
        out["form"] = await _form(session, user_id, req.date, res.tss)
    return out
