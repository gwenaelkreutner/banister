"""Moving-time estimate for a route, delegated to the `allure-cycling` library.

Banister only gathers the inputs: the route (a link, a GPX file's content, or the track of
one of the athlete's past rides on intervals.icu) and the athlete's FTP and weight, read
fresh from the data source. All the physics and its calibration live in allure-cycling
(https://github.com/gwenaelkreutner/Allure), so any other app gets the same numbers.
"""
from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from datetime import date

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

from app.engine.schemas import AthleteProfileSchema
from app.providers.intervals.athlete_profile import read_athlete_profile
from app.providers.intervals.client import IntervalsClient

DEFAULT_GROUP_SIZE = {"group": 15, "race": 80}


class RouteEstimateError(ValueError):
    """Explained to the athlete as is: the route or an input is missing or unusable."""


@dataclass(frozen=True)
class RouteRequest:
    url: str | None = None
    gpx: str | None = None
    ride_date: date | None = None
    mode: str | None = None
    watts: float | None = None
    watts_kind: str = "np"
    context: str = "solo"
    group_size: int | None = None
    temperature_c: float | None = None


def _hms(seconds: float) -> str:
    s = int(round(seconds / 60.0)) * 60
    return f"{s // 3600}h{s % 3600 // 60:02d}"


async def _ride_route(client: IntervalsClient, day: date) -> tuple[Route, str]:
    rides = [
        a for a in await client.list_activities(oldest=day.isoformat(), newest=day.isoformat())
        if a.get("type") in ("Ride", "GravelRide") and not a.get("trainer")
    ]
    if not rides:
        raise RouteEstimateError(f"no outdoor ride on {day.isoformat()}")
    ride = max(rides, key=lambda a: a.get("distance") or 0)
    raw = await client.get_activity_streams(ride["id"], types=["latlng", "altitude"])
    by_type = {s.get("type"): s for s in raw}
    latlng, alt = by_type.get("latlng"), by_type.get("altitude")
    if not latlng or not alt or not latlng.get("data2"):
        raise RouteEstimateError("that ride has no GPS track with elevation")
    lat, lon, ele = latlng["data"], latlng["data2"], alt["data"]
    pts = [(a, b, c) for a, b, c in zip(lat, lon, ele) if None not in (a, b, c)]
    if len(pts) < 2:
        raise RouteEstimateError("that ride has no GPS track with elevation")
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
    except Exception:  # the stored profile is the fallback, the response says which was used
        pass
    if stored is not None:
        ftp = ftp or stored.equipment.ftp
        weight = weight or stored.weight_kg
    return (float(ftp) if ftp else None), (float(weight) if weight else None)


def _context(req: RouteRequest):
    if req.context == "solo":
        return Solo()
    size = req.group_size or DEFAULT_GROUP_SIZE[req.context]
    if req.context == "race":
        return Race(size=size)
    return Group(size=size)


async def estimate_route(
    req: RouteRequest, client: IntervalsClient, stored: AthleteProfileSchema | None
) -> dict:
    if sum(x is not None for x in (req.url, req.gpx, req.ride_date)) != 1:
        raise RouteEstimateError("give exactly one of: url, gpx, ride_date")
    if (req.mode is None) == (req.watts is None):
        raise RouteEstimateError("give exactly one of: mode, watts")
    if req.context not in ("solo", "group", "race"):
        raise RouteEstimateError("context must be solo, group or race")

    try:
        if req.url:
            route, source = await asyncio.to_thread(load_url, req.url), req.url
        elif req.gpx:
            route, source = load_gpx(io.StringIO(req.gpx)), "GPX file"
        else:
            route, source = await _ride_route(client, req.ride_date)
    except RouteSourceError as exc:
        raise RouteEstimateError(str(exc)) from None
    except ValueError as exc:  # unreadable GPX, no elevation…
        raise RouteEstimateError(f"unusable route: {exc}") from None

    ftp, weight = await _rider(client, stored)
    if weight is None:
        raise RouteEstimateError("the athlete's weight is unknown: set it on intervals.icu")
    if req.mode is not None and ftp is None:
        raise RouteEstimateError(
            "an intensity mode needs the FTP: set it on intervals.icu, or give watts"
        )
    try:
        mode = Mode(req.mode) if req.mode is not None else None
    except ValueError:
        raise RouteEstimateError("mode must be chill, steady, tempo or race") from None

    env = Environment() if req.temperature_c is None else Environment(temperature_c=req.temperature_c)
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
        notes.append(
            "group: drafting with relays and regroupings after climbs; "
            "group constants rest on few rides"
        )
    if req.context == "race":
        notes.append("race: closed roads, sheltered in the bunch; calibrated on two races only")
    return {
        "route": {
            "source": source,
            "distance_km": round(res.distance_km, 1),
            "elevation_gain_m": round(res.elevation_gain_m),
        },
        "estimate": {
            "moving_time": _hms(res.moving_time_s),
            "moving_time_s": round(res.moving_time_s),
            "avg_speed_kmh": round(res.avg_speed_kmh, 1),
            "regroupings_min": round(res.regroup_s / 60),
            "splits": {
                name: {
                    "distance_km": round(sp.distance_km, 1),
                    "time": _hms(sp.time_s),
                    "speed_kmh": round(sp.speed_kmh, 1),
                }
                for name, sp in res.splits.items()
            },
        },
        "intensity": {
            "mode": mode.value if mode else None,
            "normalized_power_w": round(res.normalized_power_w),
            "avg_power_w": round(res.avg_power_w),
            "intensity_factor": round(res.intensity_factor, 2) if res.intensity_factor else None,
        },
        "inputs": {
            "ftp_w": ftp, "weight_kg": weight, "context": req.context,
            "group_size": getattr(ctx, "size", None), "temperature_c": env.temperature_c,
        },
        "notes": notes,
        "method": (
            "allure-cycling: Martin power model, terrain pacing, drafting; "
            "moving time only, stops excluded"
        ),
    }
