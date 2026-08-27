from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SessionSource = Literal["intervals_icu", "manual", "other"]
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

    # Contexte d'environnement (indoor/outdoor)
    environment: Literal["indoor", "outdoor"] | None = None
