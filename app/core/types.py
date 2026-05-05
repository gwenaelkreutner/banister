from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from uuid import UUID


class SportType(str, Enum):
    ride = "ride"
    run = "run"
    swim = "swim"
    other = "other"


class SessionSource(str, Enum):
    manual = "manual"
    strava = "strava"
    intervals_icu = "intervals_icu"


class Provider(str, Enum):
    intervals_icu = "intervals_icu"
    strava = "strava"
    manual = "manual"


@dataclass
class SyncedActivity:
    """Provider-agnostic activity DTO returned by any SportProvider."""

    provider: str
    provider_activity_id: str
    user_id: UUID
    activity_date: date
    sport_type: SportType
    duration_seconds: int
    tss: float | None
    # "power" | "hrss" | "provider" | "estimation"
    tss_method: str
    ctl_snapshot: float | None = None  # provider-supplied CTL (intervals.icu)
    atl_snapshot: float | None = None
    avg_watts: float | None = None
    normalized_watts: float | None = None
    avg_hr: float | None = None
    distance_m: float | None = None
    elevation_m: float | None = None
    environment: str | None = None  # "indoor" | "outdoor"
    name: str = ""
    raw: dict = field(default_factory=dict)
