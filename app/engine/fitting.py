"""Fitting — adapt a session template to an athlete's load target (spec 004
T039/T040, FR-023/FR-024).

Given a template and a target TSS, either returns steps whose duration and load
reflect the target while preserving the template's structural character (its
`ScalingRules` bounds), or refuses with a named reason. Never silently clamps —
this is the deliberate replacement for `plan_builder.py`'s old
`max(40, min(120, ...))` behaviour (research R2).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.schemas import (
    RepeatGroup,
    Step,
    Zone,
    derive_duration_minutes,
    derive_target_time_in_zone_minutes,
    derive_zone_code,
)
from app.engine.session_library import SessionTemplate
from app.engine.tss import estimate_session_tss, estimate_structured_session_tss

_DEFAULT_TOLERANCE = 0.15  # 15% — how close a fit must land to the target TSS


class FittingError(Exception):
    """Raised when a template cannot be fit to a target within its declared
    ScalingRules, or when the fit would exceed the athlete's stated availability.
    Always names the template and the constraint that failed (FR-024)."""


@dataclass
class FitResult:
    steps: list[Step | RepeatGroup]
    duration_minutes: int
    tss_target: float
    zone_code: str
    target_time_in_zone_minutes: int


def _is_interval_template(template: SessionTemplate) -> bool:
    return any(isinstance(item, RepeatGroup) for item in template.structure)


def _shape(template: SessionTemplate) -> tuple[int, int, int]:
    group = next(item for item in template.structure if isinstance(item, RepeatGroup))
    work_step = next(s for s in group.steps if s.kind == "work")
    rest_step = next((s for s in group.steps if s.kind == "recovery"), None)
    rest_minutes = rest_step.duration_minutes if rest_step else 0
    return group.repeat, work_step.duration_minutes, rest_minutes


def _warmup_cooldown(template: SessionTemplate) -> tuple[int, int]:
    warmup = next(
        (i for i in template.structure if isinstance(i, Step) and i.kind == "warmup"), None
    )
    cooldown = next(
        (i for i in template.structure if isinstance(i, Step) and i.kind == "cooldown"), None
    )
    return (
        warmup.duration_minutes if warmup else 0,
        cooldown.duration_minutes if cooldown else 0,
    )


def _materialize_interval(
    template: SessionTemplate, sets: int, work_min: int, rest_min: int,
) -> list[Step | RepeatGroup]:
    warmup_min, cooldown_min = _warmup_cooldown(template)
    group = next(item for item in template.structure if isinstance(item, RepeatGroup))
    zone = group.steps[0].zone_code

    steps: list[Step | RepeatGroup] = []
    if warmup_min:
        steps.append(Step(kind="warmup", duration_minutes=warmup_min, zone_code="Z1"))
    steps.append(RepeatGroup(repeat=sets, steps=[
        Step(kind="work", duration_minutes=work_min, zone_code=zone),
        Step(kind="recovery", duration_minutes=rest_min, zone_code="Z1"),
    ]))
    if cooldown_min:
        steps.append(Step(kind="cooldown", duration_minutes=cooldown_min, zone_code="Z1"))
    return steps


def _fit_interval(
    template: SessionTemplate,
    target_tss: float,
    coaching_mode: str,
    ftp: int | None,
    tolerance: float,
) -> FitResult:
    sets0, work0, rest0 = _shape(template)
    scaling = template.scaling
    repeat_lo, repeat_hi = (
        scaling.repeat_range if scaling and scaling.repeat_range else (sets0, sets0)
    )
    work_lo, work_hi = (
        scaling.work_minutes_range if scaling and scaling.work_minutes_range else (work0, work0)
    )

    best: tuple[list[Step | RepeatGroup], float, int, int] | None = None
    best_diff = float("inf")

    # Grid search — the ranges this library declares are always small (2-7 x
    # 4-25), so an exhaustive search is cheap and, unlike a directional
    # heuristic, guaranteed to find the true closest achievable fit.
    for sets in range(repeat_lo, repeat_hi + 1):
        for work in range(work_lo, work_hi + 1):
            steps = _materialize_interval(template, sets, work, rest0)
            tss = estimate_structured_session_tss(steps, coaching_mode, ftp)
            diff = abs(tss - target_tss)
            if diff < best_diff:
                best, best_diff = (steps, tss, sets, work), diff

    assert best is not None  # repeat/work ranges always yield >= 1 combination
    steps, tss, sets, work = best

    if best_diff > target_tss * tolerance:
        raise FittingError(
            f"template {template.id!r} cannot reach TSS {target_tss:.0f} within its "
            f"scaling bounds (repeat {repeat_lo}-{repeat_hi}, work {work_lo}-{work_hi}min) "
            f"— closest achievable is {tss:.0f} TSS (sets={sets}, work={work}min)"
        )

    return FitResult(
        steps=steps,
        duration_minutes=derive_duration_minutes(steps),
        tss_target=tss,
        zone_code=derive_zone_code(steps),
        target_time_in_zone_minutes=derive_target_time_in_zone_minutes(steps),
    )


def _fit_steady(
    template: SessionTemplate,
    target_tss: float,
    coaching_mode: str,
    ftp: int | None,
    tolerance: float,
) -> FitResult:
    steady = next(
        item for item in template.structure if isinstance(item, Step) and item.kind == "steady"
    )
    zone = steady.zone_code
    scaling = template.scaling
    lo, hi = (
        scaling.steady_minutes_range if scaling and scaling.steady_minutes_range
        else (steady.duration_minutes, steady.duration_minutes)
    )

    tss_per_60 = estimate_session_tss(zone, 60, coaching_mode, ftp)
    if tss_per_60 <= 0:
        raise FittingError(
            f"template {template.id!r}: zone {zone!r} yields zero TSS/hour — cannot fit"
        )

    duration = max(lo, min(hi, round(60 * target_tss / tss_per_60)))
    steps = [Step(kind="steady", duration_minutes=duration, zone_code=zone)]
    tss = estimate_structured_session_tss(steps, coaching_mode, ftp)

    if abs(tss - target_tss) > target_tss * tolerance:
        raise FittingError(
            f"template {template.id!r} cannot reach TSS {target_tss:.0f} within its "
            f"steady_minutes_range {lo}-{hi}min — closest achievable is "
            f"{tss:.0f} TSS at {duration}min"
        )

    return FitResult(
        steps=steps, duration_minutes=duration, tss_target=tss, zone_code=zone,
        target_time_in_zone_minutes=0,
    )


@dataclass
class ResolvedIntensity:
    zone_code: str
    lower: int | None  # watts or bpm, per `unit`
    upper: int | None
    unit: str  # "W" | "bpm"


def resolve_zone_intensity(
    zone_code: str, zones: dict[str, Zone], coaching_mode: str,
) -> ResolvedIntensity:
    """Resolves a step's zone code to absolute targets in the athlete's own terms
    (FR-026) — power or heart rate, per `coaching_mode`. `Zone` already carries
    both relative and resolved values (research R5); no new machinery needed here.

    A zone the athlete's scheme doesn't define (a template referencing a code
    outside `zones`) resolves to `lower=None, upper=None` rather than raising or
    fabricating a value — the zone *code* is still presentable even when its
    absolute bounds are not (FR-027, Constitution Principle IV). In this codebase
    today `zones` is always populated (plan generation falls back to an estimated
    threshold rather than leaving it unset — see `generate_plan()`), so this path
    covers the template-references-an-undefined-zone case specifically, not a
    "the athlete has literally no data" case that doesn't currently arise."""
    zone = zones.get(zone_code)
    unit = "W" if coaching_mode == "power" else "bpm"
    if zone is None:
        return ResolvedIntensity(zone_code=zone_code, lower=None, upper=None, unit=unit)

    if coaching_mode == "power":
        return ResolvedIntensity(
            zone_code=zone_code, lower=zone.lower_watts, upper=zone.upper_watts, unit=unit
        )
    return ResolvedIntensity(
        zone_code=zone_code, lower=zone.lower_bpm, upper=zone.upper_bpm, unit=unit
    )


def fit_template(
    template: SessionTemplate,
    target_tss: float,
    *,
    coaching_mode: str,
    ftp: int | None = None,
    available_minutes: int | None = None,
    tolerance: float = _DEFAULT_TOLERANCE,
) -> FitResult:
    """Adapts `template` to `target_tss`, preferring the smallest deviation
    achievable within its own `ScalingRules` (FR-023). Refuses — rather than
    clamping — when no combination gets within `tolerance` of the target, or
    when the fitted session exceeds `available_minutes` (FR-024)."""
    if _is_interval_template(template):
        result = _fit_interval(template, target_tss, coaching_mode, ftp, tolerance)
    else:
        result = _fit_steady(template, target_tss, coaching_mode, ftp, tolerance)

    if available_minutes is not None and result.duration_minutes > available_minutes:
        raise FittingError(
            f"template {template.id!r} fitted to {result.duration_minutes}min exceeds "
            f"the athlete's stated availability of {available_minutes}min"
        )

    return result
