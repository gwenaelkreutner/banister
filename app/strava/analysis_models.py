from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SessionSource = Literal["strava", "intervals_icu", "manual", "other"]
SportType = Literal["ride", "run", "swim", "other"]
SessionTypeReal = Literal[
    "long_ride",
    "endurance",
    "tempo",
    "intervals",
    "recovery",
    "race",
    "unknown",
]


class RawActivityStreams(BaseModel):
    time: list[int] = Field(default_factory=list)
    watts: list[float] = Field(default_factory=list)
    heartrate: list[float] = Field(default_factory=list)
    velocity_smooth: list[float] = Field(default_factory=list)
    grade_smooth: list[float] = Field(default_factory=list)
    moving: list[bool] = Field(default_factory=list)
    altitude: list[float] = Field(default_factory=list)


class RawActivity(BaseModel):
    activity_id: str
    source: SessionSource = "strava"
    sport_type: SportType
    strava_sport_type: str | None = None
    name: str | None = None
    start_datetime: datetime

    duration_s: int
    moving_time_s: int | None = None
    distance_m: float | None = None

    avg_power: float | None = None
    weighted_avg_power: float | None = None
    max_power: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_speed_m_s: float | None = None
    kilojoules: float | None = None
    suffer_score: int | None = None

    has_power: bool = False
    has_heartrate: bool = False
    has_gps: bool = False
    is_manual: bool = False  # activité saisie manuellement, pas de streams disponibles

    race_or_test: bool = False
    is_planned_session: bool = False

    average_temp: float | None = None
    total_elevation_gain: float | None = None
    athlete_count: int = 1  # >1 = sortie en groupe

    streams: RawActivityStreams | None = None


class AnalyzedSession(BaseModel):
    session_id: str
    source: SessionSource
    sport_type: SportType
    start_datetime: datetime

    duration_s: int
    moving_time_s: int | None = None
    distance_m: float | None = None
    has_power: bool
    has_heartrate: bool
    has_gps: bool

    avg_power: float | None = None
    normalized_power: float | None = None
    normalized_power_source: Literal["strava", "computed"] | None = None
    max_power: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_speed_m_s: float | None = None

    time_in_zones_s: dict[str, int] = Field(default_factory=dict)
    dominant_zone: str | None = None

    # Nullable, not defaulted to 0.0: intervals.icu returns a permanently null training load
    # for ~15% of real activities (no power, no heart rate to compute one from) — treating
    # that as a rest day would be a silent, false estimate (FR-020, spec 002 research R9c).
    tss: float | None = None
    intensity_factor: float | None = None
    variability_index: float | None = None  # NP / avg_power

    session_type_real: SessionTypeReal = "unknown"

    respect_zones_score: float | None = None
    cardiac_drift_index: float | None = None
    intervals_consistency_index: float | None = None

    planned_session_id: str | None = None
    planned_workout_type: str | None = None
    planned_zone: str | None = None
    planned_tss: float | None = None
    planned_target_time_in_zone_s: float | None = None
    plan_match_score: float | None = None

    # Présent uniquement quand HRSS détecte un écart RPE significatif (mode HR)
    fatigue_anomaly: dict | None = None

    # Contexte d'environnement (dérivé de strava_sport_type)
    environment: Literal["indoor", "outdoor"] | None = None
