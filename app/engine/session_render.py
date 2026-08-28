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

from app.engine.schemas import RepeatGroup, Step, derive_zone_code

_DEFAULT_LANGUAGE = "fr"

_ZONE_NAMES: dict[str, dict[str, str]] = {
    "fr": {
        "Z1": "Récupération active",
        "Z2": "Endurance",
        "Z3": "Tempo",
        "Z4": "Seuil lactique",
        "Z5": "VO2 Max",
        "Z6": "Anaérobie",
    },
    "en": {
        "Z1": "Active recovery",
        "Z2": "Endurance",
        "Z3": "Tempo",
        "Z4": "Lactate threshold",
        "Z5": "VO2 max",
        "Z6": "Anaerobic",
    },
}

_WORKOUT_TYPE_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "long_ride": "Sortie longue",
        "endurance": "Endurance",
        "recovery": "Récupération active",
        "intervals": "Intervalles",
    },
    "en": {
        "long_ride": "Long ride",
        "endurance": "Endurance",
        "recovery": "Active recovery",
        "intervals": "Intervals",
    },
}

# Preserved from plan_builder.py's old _session_description() (spec 004 T049) —
# real coaching content (HR lags true effort on short, sharp efforts), not
# boilerplate. A renderer rule now, keyed by language, rather than a single
# hardcoded French sentence appended unconditionally.
_HR_RPE_CAVEAT: dict[str, str] = {
    "fr": (
        "(Pilotage au RPE 9/10 — le cardio monte trop lentement sur ces "
        "intervalles courts ; suivre la sensation d'effort, pas la FC)"
    ),
    "en": (
        "(Pace by RPE 9/10 — heart rate climbs too slowly for these short "
        "intervals; follow perceived effort, not HR)"
    ),
}


def render_description(
    workout_type: str,
    steps: list[Step | RepeatGroup],
    *,
    coaching_mode: str = "hr",
    language: str = _DEFAULT_LANGUAGE,
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
    labels = _WORKOUT_TYPE_LABELS.get(language, _WORKOUT_TYPE_LABELS[_DEFAULT_LANGUAGE])
    label = labels.get(workout_type, workout_type)

    zone_code = derive_zone_code(steps)
    zone_names = _ZONE_NAMES.get(language, _ZONE_NAMES[_DEFAULT_LANGUAGE])
    zone_name = zone_names.get(zone_code, zone_code)

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
        caveat = _HR_RPE_CAVEAT.get(language, _HR_RPE_CAVEAT[_DEFAULT_LANGUAGE])
        desc += f" {caveat}"

    return desc
