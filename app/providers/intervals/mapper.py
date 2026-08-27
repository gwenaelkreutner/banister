"""intervals.icu activity payload -> AnalyzedSession (spec 002 T011).

Consumed verbatim (never recomputed — FR-015, FR-016, FR-017; research R9a/R9b/R9c), but
not always bit-for-bit as the source represents it: unit conversion happens at the
boundary, not downstream. tss (icu_training_load), normalized_power
(icu_weighted_avg_watts) and time_in_zones_s (icu_zone_times) pass straight through.
intensity_factor is icu_intensity / 100 (the source expresses it as a percentage).
variability_index equals icu_variability_index directly (verified identical, R9a).
cardiac_drift_index is decoupling / 100 — decoupling is also a percentage, while
cardiac_drift_index is a signed fraction everywhere else in this codebase (found live,
T051: an unconverted value rendered as "+1571%" in a real notification). The underlying
*number* disagreeing with our old formula (R9b) is a separate, expected fact — this is
about matching the unit our own consumers already assume, not about which value is
"more correct".

A null icu_training_load is permanent, not pending (R9c) — it stays None here, never 0.0
(FR-020). ~15% of real activities have one.

Retained as local computation (data-model.md): respect_zones_score, session_type_real,
intervals_consistency_index — the source cannot know our plan or detect our own
classification of the activity, so these are computed here rather than consumed.

icu_ctl/icu_atl are NOT mapped here even though data-model.md lists them as consumed:
AnalyzedSession has no ctl/atl/tsb fields — the current fitness state
(ctl_at_session/atl_at_session/tsb_at_session) is written straight onto SessionLog by
app/services/activity_feedback.py, from the local atl_ctl engine, after analysis.
Switching that write to consume icu_ctl/icu_atl instead is a separate, not-yet-done
change — this mapper cannot do it alone, since it never touches SessionLog itself.
"""
from __future__ import annotations

from app.providers.analysis.analysis_models import AnalyzedSession, SessionTypeReal, SportType

_SPORT_TYPE_MAP: dict[str, SportType] = {
    "Ride": "ride",
    "VirtualRide": "ride",
    "MountainBikeRide": "ride",
    "GravelRide": "ride",
    "TrackRide": "ride",
    "Cyclocross": "ride",
    "Run": "run",
    "VirtualRun": "run",
    "TrailRun": "run",
    "Swim": "swim",
    "OpenWaterSwim": "swim",
}


def _sport_type(icu_type: str | None) -> SportType:
    return _SPORT_TYPE_MAP.get(icu_type or "", "other")


def _environment(icu_type: str | None) -> str:
    return "indoor" if icu_type == "VirtualRide" else "outdoor"


def _time_in_zones(icu_zone_times: list[dict] | None) -> dict[str, int]:
    if not icu_zone_times:
        return {}
    return {entry["id"]: entry["secs"] for entry in icu_zone_times if entry.get("secs", 0) > 0}


def _dominant_zone(time_in_zones_s: dict[str, int]) -> str | None:
    # "SS" (sweet spot) is a compliance sub-bucket that overlaps Z3/Z4, not a distinct
    # rate zone — including it here would double-count time already counted elsewhere and
    # make "which zone dominated" misleading. It is still stored verbatim in
    # time_in_zones_s; only this derived pick excludes it.
    candidates = {code: secs for code, secs in time_in_zones_s.items() if code != "SS"}
    if not candidates:
        return None
    return max(candidates, key=candidates.get)


def _compute_respect_zones_score(
    time_in_zones_s: dict[str, int],
    planned_zone: str | None,
    target_time_in_zone_s: float | None,
) -> float | None:
    if not planned_zone or not target_time_in_zone_s or target_time_in_zone_s <= 0:
        return None
    if not time_in_zones_s:
        return None
    in_planned = time_in_zones_s.get(planned_zone, 0)
    score = (in_planned / target_time_in_zone_s) * 105
    return min(round(score, 2), 100.0)


def _detect_session_type(
    *,
    duration_s: int,
    time_in_zones_s: dict[str, int],
    race: bool,
    high_intensity_variation: bool,
) -> SessionTypeReal:
    total = sum(time_in_zones_s.values())
    if total <= 0:
        return "race" if race else "unknown"

    duration_h = duration_s / 3600
    z1_z2 = (time_in_zones_s.get("Z1", 0) + time_in_zones_s.get("Z2", 0)) / total
    z3_z4 = (time_in_zones_s.get("Z3", 0) + time_in_zones_s.get("Z4", 0)) / total
    z4_plus = sum(
        time_in_zones_s.get(z, 0) for z in ("Z4", "Z5", "Z6", "Z7")
    ) / total

    if z4_plus >= 0.10 and high_intensity_variation:
        return "intervals"

    tempo_threshold = 0.6 if duration_h > 3 else 0.4
    if z3_z4 >= tempo_threshold:
        return "tempo"

    if z1_z2 >= 0.6:
        if duration_s < 3600:
            return "recovery"
        elif duration_h >= 2.5:
            return "long_ride"
        else:
            return "endurance"

    if duration_h >= 2.5:
        return "long_ride"

    return "unknown"


def _has_high_intensity_variation(icu_intervals: list[dict] | None) -> bool:
    """Whether the source's own interval detection found structured hard efforts — used
    in place of our old stream-variance heuristic (T021), since intervals.icu's WORK/
    RECOVERY segmentation is a stronger signal than a P95/P05 ratio over raw watts."""
    if not icu_intervals:
        return False
    work_intervals = [iv for iv in icu_intervals if iv.get("type") == "WORK"]
    return len(work_intervals) >= 1


def _intervals_consistency_index(icu_intervals: list[dict] | None) -> float | None:
    """Rebuilt on the source's own interval detection (T022) rather than our own
    stream-block detection: the coefficient-of-variation formula is unchanged, only the
    segmentation feeding it is now intervals.icu's (icu_intervals, type == "WORK")."""
    if not icu_intervals:
        return None

    work_watts = [
        iv["average_watts"]
        for iv in icu_intervals
        if iv.get("type") == "WORK" and iv.get("average_watts")
    ]
    if len(work_watts) < 2:
        return None

    block_mean = sum(work_watts) / len(work_watts)
    if block_mean <= 0:
        return None

    variance = sum((w - block_mean) ** 2 for w in work_watts) / len(work_watts)
    cv = (variance**0.5) / block_mean
    return round(max(0.0, min(1.0, 1.0 - cv)), 4)


def map_activity_to_analyzed_session(
    payload: dict,
    *,
    planned_session_id: str | None = None,
    planned_workout_type: str | None = None,
    planned_zone: str | None = None,
    planned_tss: float | None = None,
    planned_target_time_in_zone_s: float | None = None,
) -> AnalyzedSession:
    icu_type = payload.get("type")
    icu_zone_times = payload.get("icu_zone_times")
    icu_intervals = payload.get("icu_intervals")

    time_in_zones_s = _time_in_zones(icu_zone_times)
    dominant_zone = _dominant_zone(time_in_zones_s)

    icu_intensity = payload.get("icu_intensity")
    intensity_factor = (icu_intensity / 100) if icu_intensity is not None else None

    # decoupling is already a percentage (15.7 == 15.7%), but cardiac_drift_index is a
    # signed fraction throughout the rest of this codebase (highlight.py's threshold is
    # 0.08, i.e. 8%) — found live: an unconverted value rendered as "+1571%" in a real
    # notification. Divide to match the unit every consumer already assumes.
    icu_decoupling = payload.get("decoupling")
    cardiac_drift_index = (icu_decoupling / 100) if icu_decoupling is not None else None

    duration_s = int(payload.get("elapsed_time") or 0)

    respect_zones_score = _compute_respect_zones_score(
        time_in_zones_s, planned_zone, planned_target_time_in_zone_s
    )
    session_type_real = _detect_session_type(
        duration_s=duration_s,
        time_in_zones_s=time_in_zones_s,
        race=bool(payload.get("race")),
        high_intensity_variation=_has_high_intensity_variation(icu_intervals),
    )

    return AnalyzedSession(
        session_id=str(payload["id"]),
        source="intervals_icu",
        sport_type=_sport_type(icu_type),
        start_datetime=payload["start_date"],
        duration_s=duration_s,
        moving_time_s=payload.get("moving_time"),
        distance_m=payload.get("distance"),
        has_power=bool(payload.get("device_watts")),
        has_heartrate=bool(payload.get("has_heartrate")),
        has_gps="latlng" in (payload.get("stream_types") or []),
        avg_power=payload.get("icu_average_watts"),
        normalized_power=payload.get("icu_weighted_avg_watts"),
        max_power=None,  # not exposed per-activity by the source (research R9a)
        avg_hr=payload.get("average_heartrate"),
        max_hr=payload.get("max_heartrate"),
        avg_speed_m_s=payload.get("average_speed"),
        time_in_zones_s=time_in_zones_s,
        dominant_zone=dominant_zone,
        tss=payload.get("icu_training_load"),
        intensity_factor=intensity_factor,
        variability_index=payload.get("icu_variability_index"),
        session_type_real=session_type_real,
        respect_zones_score=respect_zones_score,
        cardiac_drift_index=cardiac_drift_index,
        intervals_consistency_index=_intervals_consistency_index(icu_intervals),
        planned_session_id=planned_session_id,
        planned_workout_type=planned_workout_type,
        planned_zone=planned_zone,
        planned_target_time_in_zone_s=planned_target_time_in_zone_s,
        planned_tss=planned_tss,
        plan_match_score=None,
        fatigue_anomaly=None,
        environment=_environment(icu_type),
    )
