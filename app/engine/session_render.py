"""Session description rendering — produces text from a session's structure
rather than retrieving it from generation-embedded strings (spec 004
T047-T049, FR-028/FR-029/FR-030).

Scope boundary recorded in research.md R6 and honored here: full end-to-end
language switching driven by the athlete's configured coach voice is spec
007's job (`app/core/persona.py::load_persona()` exists but is called from
nowhere yet — both specs name that wiring as a shared prerequisite, and doing
it twice would produce two different answers). This module's obligation is
narrower and concrete: nothing it builds itself is fixed to one language, and
it demonstrably follows a `language` parameter when given one.
"""
from __future__ import annotations

from app.core.localization import t
from app.engine.schemas import RepeatGroup, SessionSpec, Step, derive_zone_code

def render_description(
    workout_type: str,
    steps: list[Step | RepeatGroup],
    *,
    coaching_mode: str = "hr",
    language: str = "fr",
    detail: str = "",
) -> str:
    """Produces a session description from its actual steps (FR-028).

    The interval shape (sets × work-minutes, zone) is read from the real
    `RepeatGroup` rather than trusted to match a hand-typed label — the old
    `plan_builder.py` code accepted a `detail` string that *should* have agreed
    with the session's real structure but was never checked against it (a real
    duplication risk this eliminates by construction: the description and the
    steps now share one source).

    `detail` carries narrative flavor with no structural counterpart — endurance
    cadence variants, "with threshold blocks simulating a climb" race context —
    appended as-is in whatever language the caller already localized it. Full
    localization of that free text belongs to spec 007 (see module docstring);
    this function's own output never is fixed to one language."""
    label = t(f"session.workout.{workout_type}", language=language)

    zone_code = derive_zone_code(steps)
    zone_name = t(f"session.zone.{zone_code}", language=language)

    if workout_type == "intervals":
        group = next((item for item in steps if isinstance(item, RepeatGroup)), None)
        if group is not None:
            work_step = next(s for s in group.steps if s.kind == "work")
            base = (
                f"{label} {group.repeat}×{work_step.duration_minutes}min "
                f"{zone_code} ({zone_name})"
            )
        else:
            base = f"{label} {zone_code} ({zone_name})"
    else:
        base = f"{label} {zone_code} ({zone_name})"

    # Matches the old code's behaviour exactly: recovery sessions never show a
    # detail suffix even if one were passed (no call site does today, but the
    # asymmetry is preserved rather than silently changed).
    desc = f"{base} — {detail}" if (detail and workout_type != "recovery") else base

    if zone_code in ("Z5", "Z6") and coaching_mode == "hr":
        caveat = t("session.hr_rpe_caveat", language=language)
        desc += f" {caveat}"

    return desc


def render_session_description(
    session: SessionSpec,
    *,
    coaching_mode: str = "hr",
    language: str = "fr",
) -> str:
    """Display a stored session without changing its persisted French summary.

    Legacy sessions have no steps from which to recover their precise meaning.
    Race-week sessions carry event instructions beyond their workout structure;
    retain these instructions when switching languages.
    """
    if session.steps is None:
        return session.description_fr

    description = session.description_fr
    if language == "fr":
        return description

    if description.startswith("JOUR DE COURSE"):
        return t("session.race_day", language=language, duration=session.duration_minutes)

    rendered = render_description(
        session.workout_type,
        session.steps,
        coaching_mode=coaching_mode,
        language=language,
    )
    if description.startswith("Activation pre-course"):
        return t("session.pre_race_activation", language=language, description=rendered)
    if description.startswith("Endurance Z2 — 120min en zone 2"):
        return t("session.pre_race_endurance", language=language, description=rendered,
                 duration=session.duration_minutes)
    return rendered
