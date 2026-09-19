"""Freestyle session selection (spec 009, US1) — deterministic, zero LLM (Constitution
Principle I).

Replaces periodization `phase` with the athlete's current fitness state and recent
training load as the input to session selection. Where `plan_builder.py` asks
`session_library.select_template(phase, workout_type, week_in_block)` because a plan
gives it a phase, freestyle mode has no plan and therefore no phase — this module picks
`(workout_type, target_tss)` from CTL/ATL/TSB and recent effort history instead, then
hands off to `fitting.fit_template()`, which was already phase-agnostic (spec 004, never
wired to `generate_plan()` until now).

The exact thresholds below are a first deterministic approximation (flagged as an open
engineering question in `specs/009-freestyle-coaching-mode/research.md`) — TSB bands are
intentionally the same ones already shown to the athlete via `atl_ctl.tsb_label()`, so a
freestyle suggestion never contradicts what `/forme` just told them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.engine.atl_ctl import FitnessMetrics
from app.engine.fitting import FitResult, FittingError, fit_template
from app.engine.session_library import SessionLibraryError, SessionTemplate, load_library
from app.engine.weekly_snapshot import WeeklySnapshot

VALID_WORKOUT_TYPES = frozenset({"long_ride", "intervals", "endurance", "recovery"})

# Target TSS per workout type, as a multiplier of CTL (an EMA of daily TSS — a
# reasonable single-session anchor) with a floor for very-low-CTL athletes so a
# suggestion is never a near-zero, meaningless duration.
_TSS_FLOOR = {"recovery": 20.0, "endurance": 30.0, "intervals": 35.0, "long_ride": 40.0}
_TSS_CTL_MULTIPLIER = {"recovery": 0.4, "endurance": 0.9, "intervals": 0.85, "long_ride": 1.4}

# A hard effort in the last HARD_EFFORT_COOLDOWN_DAYS days rules out another
# intervals/long_ride day, favoring endurance instead — mirrors the "hard" vocabulary
# guardrail_service.py already uses (`_HARD_WORKOUT_TYPES`).
HARD_EFFORT_COOLDOWN_DAYS = 2

# TSS/hour above this is treated as a "hard" effort for cooldown purposes. 100 TSS/hour
# is threshold power by definition (Coggan) — a lower bar catches sweet-spot/VO2 work
# too, without flagging genuine endurance/recovery riding. This doubles as the proxy for
# `Activity` rows (pre-plan imports), which carry no `session_type_real` classification
# the way a `SessionLog` does — TSS/hour is the one intensity signal both types share.
_HARD_EFFORT_TSS_PER_HOUR = 70.0


def days_since_hard_effort(items: list, today: date) -> int | None:
    """Duck-types over `SessionLog`/`Activity` (same pattern as `weekly_snapshot.py`).
    `None` when no qualifying effort is found in `items` at all — the caller then
    treats the athlete as not recently having done anything hard."""
    best: int | None = None
    for it in items:
        tss = getattr(it, "tss_actual", None) or getattr(it, "tss", None)
        if not tss:
            continue
        duration_min = getattr(it, "duration_minutes_actual", None)
        if duration_min is None:
            duration_s = getattr(it, "duration_seconds", None)
            duration_min = duration_s / 60 if duration_s else None
        if not duration_min:
            continue
        if (tss / (duration_min / 60)) < _HARD_EFFORT_TSS_PER_HOUR:
            continue

        item_date = getattr(it, "logged_date", None) or getattr(it, "activity_date", None)
        if item_date is None or item_date > today:
            continue
        gap = (today - item_date).days
        if best is None or gap < best:
            best = gap
    return best


@dataclass(frozen=True)
class WorkoutTypeChoice:
    workout_type: str
    target_tss: float
    reasoning_summary: str
    preference_overridden: bool  # True if every preferred type was in avoid_workout_types
    default_conflicts: bool  # True if chosen != what fitness alone would have picked (spec 011)


@dataclass(frozen=True)
class FreestyleSuggestion:
    workout_type: str
    template_id: str
    steps: list
    duration_minutes: int
    target_tss: float
    zone_code: str
    reasoning_summary: str


class NoSuitableTemplateError(Exception):
    """Raised when no template in the library can be fit to the chosen workout type at
    all, even after relaxing fitting tolerance — the caller must report this rather than
    guess a session (FR-011 / Constitution Principle IV)."""


def _tsb_bucket_preferences(tsb: float, days_since_hard_effort: int | None) -> list[str]:
    """Ordered workout-type preference for the current TSB band, aligned with the bands
    `atl_ctl.tsb_label()` already shows the athlete (never contradict `/forme`)."""
    recently_hard = (
        days_since_hard_effort is not None
        and days_since_hard_effort < HARD_EFFORT_COOLDOWN_DAYS
    )

    if tsb < -30:  # surmenage
        return ["recovery", "endurance"]
    if tsb < 0:  # fatigue normale
        return ["endurance", "recovery"]
    if tsb < 15:  # bonne forme / forme de pointe
        if recently_hard:
            return ["endurance", "long_ride", "recovery"]
        return ["intervals", "endurance", "long_ride"]
    # très frais / transition (tsb >= 15)
    if recently_hard:
        return ["long_ride", "endurance"]
    return ["intervals", "long_ride", "endurance"]


def choose_workout_type(
    fitness: FitnessMetrics,
    snapshot: WeeklySnapshot,
    *,
    days_since_hard_effort: int | None,
    avoid_workout_types: frozenset[str] = frozenset(),
    requested_workout_type: str | None = None,
) -> WorkoutTypeChoice:
    """Pure decision: given fitness state + recent load, which workout type and target
    TSS to suggest. Never references a periodization phase or week (SC-005) — the only
    inputs are today's fitness and effort history, exactly as the athlete would explain
    their own choice ("je suis cuit, je vais rouler tranquille").

    `requested_workout_type` (spec 011) is an explicit, same-turn ask from the athlete —
    it wins outright and bypasses `avoid_workout_types` entirely, since a fresh request is
    more specific than a standing dislike note (FR-009). `default_conflicts` tells the
    caller whether this diverges from what fitness alone would have suggested, so the
    coach can say so honestly instead of presenting it as the natural choice (FR-002)."""
    preferences = _tsb_bucket_preferences(fitness.tsb, days_since_hard_effort)

    was_requested = (
        requested_workout_type is not None and requested_workout_type in VALID_WORKOUT_TYPES
    )
    if was_requested:
        chosen = requested_workout_type
        overridden = False
    else:
        chosen = next((wt for wt in preferences if wt not in avoid_workout_types), None)
        overridden = chosen is None
        if chosen is None:
            # Every preferred type is on the avoid list — honesty over silence: pick the
            # top preference anyway rather than refusing to answer, and say so (the tool
            # layer surfaces `preference_overridden` to the athlete).
            chosen = preferences[0]

    target_tss = round(
        max(_TSS_FLOOR[chosen], fitness.ctl * _TSS_CTL_MULTIPLIER[chosen]), 0
    )

    reasoning = (
        f"TSB {fitness.tsb:+.0f}, charge des 7 derniers jours {snapshot.tss_7d:.0f} TSS "
        f"→ séance de type {chosen}."
    )
    return WorkoutTypeChoice(
        workout_type=chosen,
        target_tss=target_tss,
        reasoning_summary=reasoning,
        preference_overridden=overridden,
        # Only an explicit request can "conflict" with the fitness-driven default — the
        # avoid-list branch above already has its own honesty signal (preference_overridden)
        # and must never also trip this one, or FR-007's no-request path would regress.
        default_conflicts=was_requested and chosen != preferences[0],
    )


def _candidates_for(workout_type: str) -> list[SessionTemplate]:
    candidates = [t for t in load_library() if t.workout_type == workout_type]
    if not candidates:
        raise SessionLibraryError(
            f"no template found for workout_type={workout_type!r} — freestyle mode has "
            "a library coverage gap"
        )
    return sorted(candidates, key=lambda t: t.id)


def build_freestyle_suggestion(
    fitness: FitnessMetrics,
    snapshot: WeeklySnapshot,
    *,
    coaching_mode: str,
    ftp: int | None,
    days_since_hard_effort: int | None,
    avoid_workout_types: frozenset[str] = frozenset(),
    day_ordinal: int = 0,
    available_minutes: int | None = None,
    requested_workout_type: str | None = None,
    requested_template_id: str | None = None,
) -> FreestyleSuggestion:
    """End to end: choose a workout type (`choose_workout_type`), pick a template for it
    (rotated deterministically by `day_ordinal` — same day, same ask, same answer; a new
    day can vary the template), and fit it to the target TSS (`fitting.fit_template()`,
    unchanged, already phase-agnostic).

    `requested_workout_type`/`requested_template_id` (spec 011) let an explicit, same-turn
    athlete request steer this same deterministic pipeline — never a second, LLM-driven
    calculation, only different inputs to the same math (Constitution Principle I). A
    `requested_template_id` that isn't a candidate of the *resolved* workout type (wrong
    type, unknown id) is silently ignored — the existing `day_ordinal` rotation applies as
    if it had never been supplied (FR-005).
    """
    choice = choose_workout_type(
        fitness, snapshot,
        days_since_hard_effort=days_since_hard_effort,
        avoid_workout_types=avoid_workout_types,
        requested_workout_type=requested_workout_type,
    )
    candidates = _candidates_for(choice.workout_type)
    # Start from the day-rotated candidate for variety, but a fixed-duration template
    # (no `scaling:` declared) may simply not reach today's target TSS — try the rest of
    # the same workout_type's templates, in rotation order, before giving up entirely
    # (found live: "race-endurance" is a fixed 120min template that can only ever hit
    # ~84 TSS, which is not this athlete's target every day).
    start = day_ordinal % len(candidates)
    if requested_template_id is not None:
        for i, candidate in enumerate(candidates):
            if candidate.id == requested_template_id:
                start = i
                break
    rotated = candidates[start:] + candidates[:start]

    reasoning = choice.reasoning_summary
    if choice.preference_overridden:
        reasoning += (
            " (aucun type non évité ne convenait à ta forme actuelle — je propose quand "
            "même celui qui convient le mieux.)"
        )
    if choice.default_conflicts:
        reasoning += (
            " Ce n'est pas ce que je t'aurais proposé spontanément vu ta forme actuelle, "
            "mais voici une séance adaptée à ta demande."
        )

    last_error: NoSuitableTemplateError | None = None
    for template in rotated:
        try:
            result = _fit_with_relaxed_tolerance(
                template, choice.target_tss, coaching_mode, ftp, available_minutes
            )
            break
        except NoSuitableTemplateError as exc:
            last_error = exc
    else:
        raise NoSuitableTemplateError(
            f"no template for workout_type={choice.workout_type!r} could be fit to "
            f"{choice.target_tss:.0f} TSS: {last_error}"
        )

    return FreestyleSuggestion(
        workout_type=choice.workout_type,
        template_id=template.id,
        steps=result.steps,
        duration_minutes=result.duration_minutes,
        target_tss=result.tss_target,
        zone_code=result.zone_code,
        reasoning_summary=reasoning,
    )


def _fit_with_relaxed_tolerance(
    template: SessionTemplate,
    target_tss: float,
    coaching_mode: str,
    ftp: int | None,
    available_minutes: int | None,
) -> FitResult:
    """`fit_template()` refuses rather than clamps (spec 004 design) — reasonable for a
    plan, where a bad fit means picking a different template next time `plan_builder`
    runs. Freestyle mode has nothing to fall back to but the same template, so this
    tries progressively looser tolerance before giving up entirely (`NoSuitableTemplateError`,
    the caller's cue to say "not available" rather than crash)."""
    last_error: FittingError | None = None
    for tolerance in (0.15, 0.30, 0.50):
        try:
            return fit_template(
                template, target_tss,
                coaching_mode=coaching_mode, ftp=ftp,
                available_minutes=available_minutes, tolerance=tolerance,
            )
        except FittingError as exc:
            last_error = exc
    raise NoSuitableTemplateError(
        f"template {template.id!r} could not be fit to {target_tss:.0f} TSS "
        f"even at relaxed tolerance: {last_error}"
    )
