from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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


# ── Structured sessions (spec 004) ─────────────────────────────────────────────
#
# No absolute intensity is ever stored on a Step — no watts, no bpm, only a zone
# code. This is what makes FR-025 true by construction: when the athlete's
# threshold changes, every future session retargets automatically because nothing
# absolute was ever persisted, and completed history is untouched because it
# lives in session_logs, not in the plan (spec 004 data-model.md).


class Step(BaseModel):
    kind: Literal["warmup", "work", "recovery", "cooldown", "steady"]
    duration_minutes: int
    zone_code: str  # relative only — resolved to watts/bpm at presentation time

    @field_validator("duration_minutes")
    @classmethod
    def _duration_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Step.duration_minutes must be > 0 — a zero-length step is not a step")
        return v


class RepeatGroup(BaseModel):
    """An ordered set of steps performed `repeat` times. Does not nest — nothing in the
    current session vocabulary needs it (spec 004 research R4), and forbidding it keeps
    duration derivation a single pass."""

    repeat: int
    steps: list[Step]

    @field_validator("repeat")
    @classmethod
    def _repeat_at_least_two(cls, v: int) -> int:
        if v < 2:
            raise ValueError(
                "RepeatGroup.repeat must be >= 2 — a count of 1 is not a repetition; "
                "express it as plain steps so one structure has one representation"
            )
        return v

    @field_validator("steps")
    @classmethod
    def _steps_non_empty(cls, v: list["Step"]) -> list["Step"]:
        if not v:
            raise ValueError("RepeatGroup.steps must not be empty")
        return v


def _flatten_steps(steps: list[Step | RepeatGroup]) -> list[tuple[Step, int]]:
    """Yields (step, multiplier) pairs — multiplier is `repeat` for a step inside a
    RepeatGroup, else 1. The single place every derivation below reads structure from,
    so a change to how groups flatten cannot happen in two places and drift apart."""
    flat: list[tuple[Step, int]] = []
    for item in steps:
        if isinstance(item, RepeatGroup):
            for s in item.steps:
                flat.append((s, item.repeat))
        else:
            flat.append((item, 1))
    return flat


def derive_duration_minutes(steps: list[Step | RepeatGroup]) -> int:
    """Σ step.duration_minutes, with a RepeatGroup contributing repeat × Σ inner
    durations (spec 004 FR-005) — replaces plan_builder's old silent clamp."""
    return sum(s.duration_minutes * mult for s, mult in _flatten_steps(steps))


def derive_zone_code(steps: list[Step | RepeatGroup]) -> str:
    """The zone of the work steps; for a session whose only step is `steady`, that
    step's zone (spec 004 data-model.md). Work steps within one session share a single
    zone in every template this feature ships, so the first one found is authoritative."""
    flat = _flatten_steps(steps)
    for kind in ("work", "steady"):
        match = next((s.zone_code for s, _ in flat if s.kind == kind), None)
        if match is not None:
            return match
    return flat[0][0].zone_code if flat else ""


def derive_target_time_in_zone_minutes(steps: list[Step | RepeatGroup]) -> int:
    """Total minutes spent in the dominant work zone. Only `work` steps count — a
    `steady` session (endurance/long_ride/recovery) has no interval target, matching
    plan_builder's existing `_target_time_in_zone_minutes()` behaviour (0 unless
    workout_type == "intervals"), which this derivation must not disagree with (FR-008)."""
    flat = _flatten_steps(steps)
    work = [(s, m) for s, m in flat if s.kind == "work"]
    if not work:
        return 0
    zone = work[0][0].zone_code
    return sum(s.duration_minutes * m for s, m in work if s.zone_code == zone)


class SessionSpec(BaseModel):
    day_of_week: int  # 0=Lundi, 6=Dimanche
    workout_type: Literal["long_ride", "intervals", "endurance", "recovery"]
    zone_code: str  # "Z2", "Z4", etc.
    duration_minutes: int
    target_time_in_zone_minutes: int
    tss_target: float
    description_fr: str

    # Optional — a session loaded without steps ("legacy") keeps its stored summary
    # verbatim and validates successfully (spec 004 FR-013). Anything requiring steps
    # reports their absence rather than fabricating them (FR-014): the structure that
    # would be needed was discarded when the plan was generated, and inventing it would
    # be exactly the silent estimation Constitution Principle IV forbids.
    steps: list[Step | RepeatGroup] | None = None

    @model_validator(mode="after")
    def _summary_matches_steps(self) -> "SessionSpec":
        """When steps are present, duration/zone/time-in-zone MUST equal their
        derivation (FR-009) — checked here, at load time, rather than left to drift
        silently. tss_target is deliberately NOT checked here: its derivation needs
        coaching_mode, which is a plan-level concern SessionSpec does not carry: see
        app/engine/tss.py::estimate_structured_session_tss() and its callers in
        plan_builder.py/session_library.py/fitting.py, which are what enforce it at
        construction time instead (spec 004 data-model.md, "Why the summary stays
        stored rather than becoming computed properties")."""
        if self.steps is None:
            return self

        expected_duration = derive_duration_minutes(self.steps)
        if self.duration_minutes != expected_duration:
            raise ValueError(
                f"SessionSpec.duration_minutes ({self.duration_minutes}) disagrees with "
                f"its steps ({expected_duration}) — the two must never diverge"
            )

        expected_zone = derive_zone_code(self.steps)
        if self.zone_code != expected_zone:
            raise ValueError(
                f"SessionSpec.zone_code ({self.zone_code!r}) disagrees with its steps "
                f"({expected_zone!r})"
            )

        expected_time_in_zone = derive_target_time_in_zone_minutes(self.steps)
        if self.target_time_in_zone_minutes != expected_time_in_zone:
            raise ValueError(
                f"SessionSpec.target_time_in_zone_minutes ({self.target_time_in_zone_minutes}) "
                f"disagrees with its steps ({expected_time_in_zone})"
            )

        return self


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
    # Métriques de forme actuelles (calculées depuis l'historique importé si disponible)
    current_ctl: float | None = None  # fitness (CTL journalier, ex: 65.0)
    current_atl: float | None = None  # fatigue aiguë (ATL journalier)
    current_tsb: float | None = None  # forme = CTL - ATL
