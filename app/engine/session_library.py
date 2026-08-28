"""Session template library — loads sessions/*.yaml (spec 004 T030/T031, FR-015..021).

Templates are the only place session structure is authored; plan_builder.py selects
from here instead of constructing sessions inline (FR-018). Adding a template
requires no change to this file or to plan_builder.py (FR-016) — see
sessions/README.md and specs/004-structured-workouts/contracts/session-library.md
for the authored format contract.

Imports nothing from app/bot/ or app/llm/ (Constitution Principle III).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from app.engine.schemas import RepeatGroup, Step

SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "sessions"

_VALID_PHASES = {"base", "build", "peak", "taper"}
_VALID_WORKOUT_TYPES = {"long_ride", "intervals", "endurance", "recovery"}
_REQUIRED_FIELDS = ("workout_type", "family", "phases", "purpose", "intent", "suits", "structure")


class SessionLibraryError(Exception):
    """Raised at load time (a broken template) or at selection time (nothing
    matches a request) — always names enough to fix the problem without reading
    this module's source (contracts/session-library.md's guarantee)."""


@dataclass(frozen=True)
class ScalingRules:
    """Bounds within which fitting (app/engine/fitting.py, Phase 7) may adapt a
    template while preserving its structural character. Declared per-template so a
    contributor can set elasticity without touching generator or fitting code."""

    repeat_range: tuple[int, int] | None = None
    work_minutes_range: tuple[int, int] | None = None
    steady_minutes_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class SessionTemplate:
    id: str
    workout_type: str
    family: str
    phases: tuple[str, ...]
    purpose: str
    intent: str
    suits: str
    structure: tuple  # tuple[Step | RepeatGroup, ...]
    scaling: ScalingRules | None
    source_file: str


def _parse_step(raw: dict, *, template_id: str, source_file: str) -> Step:
    try:
        return Step(
            kind=raw["kind"], duration_minutes=raw["duration_minutes"], zone_code=raw["zone_code"]
        )
    except Exception as exc:
        raise SessionLibraryError(
            f"{source_file}: template {template_id!r}: invalid step {raw!r} — {exc}"
        ) from exc


def _parse_structure_item(raw: dict, *, template_id: str, source_file: str) -> Step | RepeatGroup:
    if "repeat" in raw:
        try:
            steps = [
                _parse_step(s, template_id=template_id, source_file=source_file)
                for s in raw.get("steps", [])
            ]
            return RepeatGroup(repeat=raw["repeat"], steps=steps)
        except SessionLibraryError:
            raise
        except Exception as exc:
            raise SessionLibraryError(
                f"{source_file}: template {template_id!r}: invalid repeat group {raw!r} — {exc}"
            ) from exc
    return _parse_step(raw, template_id=template_id, source_file=source_file)


def _parse_scaling(raw: dict | None) -> ScalingRules | None:
    if not raw:
        return None
    return ScalingRules(
        repeat_range=(
            tuple(raw["repeat_range"]) if raw.get("repeat_range") else None
        ),
        work_minutes_range=(
            tuple(raw["work_minutes_range"]) if raw.get("work_minutes_range") else None
        ),
        steady_minutes_range=(
            tuple(raw["steady_minutes_range"]) if raw.get("steady_minutes_range") else None
        ),
    )


def _parse_template(raw: dict, *, source_file: str) -> SessionTemplate:
    template_id = raw.get("id")
    if not template_id:
        raise SessionLibraryError(f"{source_file}: a template is missing required field 'id'")

    for field in _REQUIRED_FIELDS:
        if not raw.get(field):
            raise SessionLibraryError(
                f"{source_file}: template {template_id!r} is missing required field {field!r}"
            )

    if raw["workout_type"] not in _VALID_WORKOUT_TYPES:
        raise SessionLibraryError(
            f"{source_file}: template {template_id!r} has invalid workout_type "
            f"{raw['workout_type']!r} — must be one of {sorted(_VALID_WORKOUT_TYPES)}"
        )

    phases = tuple(raw["phases"])
    invalid_phases = set(phases) - _VALID_PHASES
    if invalid_phases:
        raise SessionLibraryError(
            f"{source_file}: template {template_id!r} has invalid phases {sorted(invalid_phases)} "
            f"— must be a subset of {sorted(_VALID_PHASES)}"
        )

    structure = tuple(
        _parse_structure_item(item, template_id=template_id, source_file=source_file)
        for item in raw["structure"]
    )

    return SessionTemplate(
        id=template_id,
        workout_type=raw["workout_type"],
        family=raw["family"],
        phases=phases,
        purpose=raw["purpose"],
        intent=raw["intent"],
        suits=raw["suits"],
        structure=structure,
        scaling=_parse_scaling(raw.get("scaling")),
        source_file=source_file,
    )


@cache
def load_library() -> tuple[SessionTemplate, ...]:
    """Loads and validates every template in sessions/*.yaml. Cached — restart the
    app to pick up edits (same convention as app/core/persona.py::load_persona()).
    Duplicate ids across any two files is a load error naming both files."""
    templates: list[SessionTemplate] = []
    seen_ids: dict[str, str] = {}

    for path in sorted(SESSIONS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in data.get("templates", []):
            template = _parse_template(raw, source_file=path.name)
            if template.id in seen_ids:
                raise SessionLibraryError(
                    f"duplicate template id {template.id!r} in {path.name} "
                    f"(already defined in {seen_ids[template.id]})"
                )
            seen_ids[template.id] = path.name
            templates.append(template)

    return tuple(templates)


def select_template(
    phase: str,
    workout_type: str,
    week_in_block: int,
    family: str | None = None,
) -> SessionTemplate:
    """Selects a template for (phase, workout_type[, family]). Candidates sorted by
    id, then rotated by week_in_block — repeatable regardless of filesystem order
    (FR-020), preserving week-to-week variety the way the old
    `week_in_block % len(STRUCTURES)` rotation did. Raises SessionLibraryError
    naming the unsatisfied request when nothing matches (FR-021)."""
    candidates = [
        t for t in load_library()
        if phase in t.phases and t.workout_type == workout_type
        and (family is None or t.family == family)
    ]
    if not candidates:
        raise SessionLibraryError(
            f"no template found for phase={phase!r} workout_type={workout_type!r} "
            f"family={family!r} — the library has a coverage gap"
        )
    candidates.sort(key=lambda t: t.id)
    return candidates[week_in_block % len(candidates)]
