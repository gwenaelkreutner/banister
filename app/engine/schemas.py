from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Zone(BaseModel):
    name: str
    code: str  # Z1, Z2, ...
    lower_pct: float
    upper_pct: float
    lower_watts: int | None = None
    upper_watts: int | None = None
    lower_bpm: int | None = None
    upper_bpm: int | None = None
    description_fr: str


class SessionSpec(BaseModel):
    day_of_week: int  # 0=Lundi, 6=Dimanche
    workout_type: Literal["long_ride", "intervals", "endurance", "recovery"]
    zone_code: str  # "Z2", "Z4", etc.
    duration_minutes: int
    target_time_in_zone_minutes: int
    tss_target: float
    description_fr: str


class WeekPlan(BaseModel):
    week_number: int
    phase: Literal["base", "build", "peak", "taper"]
    is_recovery_week: bool
    total_tss_target: float
    sessions: list[SessionSpec]
    start_date: date | None = None


class TrainingPlanSchema(BaseModel):
    weeks: list[WeekPlan]
    zones: dict[str, Zone]
    initial_weekly_tss: float
    peak_weekly_tss: float
    weeks_count: int
    coaching_mode: Literal["power", "hr"]
    start_date: date | None = None
    end_date: date | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Schéma profil (construit depuis session_data onboarding) ──────────────────


class ObjectiveProfile(BaseModel):
    type: Literal["event", "fitness", "performance", "other"]
    target_date: date | None = None


class AvailabilityProfile(BaseModel):
    hours_per_week: float
    preferred_days: list[str]  # ["monday", "tuesday", ...]


class EquipmentProfile(BaseModel):
    power_meter: bool
    ftp: int | None = None
    ftp_source: Literal["declared", "estimated"] = "estimated"


class PhysioProfile(BaseModel):
    age: int
    hr_max: int
    hr_max_source: Literal["declared", "estimated"] = "estimated"
    hr_rest: int
    hr_rest_source: Literal["declared", "estimated"] = "estimated"


class AthleteProfileSchema(BaseModel):
    objective: ObjectiveProfile
    availability: AvailabilityProfile
    level: Literal["beginner", "intermediate", "advanced", "expert"]
    structured_plan_history: bool
    equipment: EquipmentProfile
    physio: PhysioProfile
    coaching_mode: Literal["power", "hr"]
    health_constraints: bool
    user_level: int = 0  # 0=Débutant, 1=Amateur, 2=Intermédiaire (vocabulaire LLM)
    injury_status: dict | None = None
    # {is_injured, location, severity, zone_restrictions, start_date, estimated_recovery_date}
    weight_kg: float | None = None
    sex: str | None = None  # "M" | "F" | None
    # Métriques de forme actuelles (calculées depuis l'historique Strava si disponible)
    current_ctl: float | None = None  # fitness (CTL journalier, ex: 65.0)
    current_atl: float | None = None  # fatigue aiguë (ATL journalier)
    current_tsb: float | None = None  # forme = CTL - ATL
