"""render_dsl() — Step/RepeatGroup -> intervals.icu workout DSL text.

The reference shape is research.md R3's live-verified probe: posting this exact text to
the real API produced the `workout_doc` recorded in contracts/calendar-publication.md §1.
We can't reproduce the server-side parse in a unit test, so we assert the text we send
matches the shape that parse was verified against.
"""
from __future__ import annotations

import pytest

from app.engine.schemas import RepeatGroup, Step, Zone
from app.providers.intervals.workout_dsl import EmptySessionError, render_dsl


def _zone(code: str, lower: float, upper: float) -> Zone:
    return Zone(
        name=code,
        code=code,
        lower_pct=lower,
        upper_pct=upper,
        description_fr="",
    )


# Zones chosen so the rendered percentages match R3's verified probe exactly.
_R3_ZONES = {
    "ZW": _zone("ZW", 0.50, 0.65),
    "Z4": _zone("Z4", 0.88, 0.94),
    "ZR": _zone("ZR", 0.50, 0.50),
    "ZC": _zone("ZC", 0.50, 0.50),
}

_R3_EXPECTED = (
    "Warmup\n"
    "- 15m 50-65%\n"
    "\n"
    "Main set\n"
    "3x\n"
    "- 12m 88-94%\n"
    "- 4m 50%\n"
    "\n"
    "Cooldown\n"
    "- 15m 50%"
)


def test_renders_r3_reference_shape():
    steps = [
        Step(kind="warmup", duration_minutes=15, zone_code="ZW"),
        RepeatGroup(
            repeat=3,
            steps=[
                Step(kind="work", duration_minutes=12, zone_code="Z4"),
                Step(kind="recovery", duration_minutes=4, zone_code="ZR"),
            ],
        ),
        Step(kind="cooldown", duration_minutes=15, zone_code="ZC"),
    ]
    assert render_dsl(steps, _R3_ZONES) == _R3_EXPECTED


def test_repeat_group_renders_full_inner_unit_including_final_recovery():
    """R4: intervals.icu computes the same total duration as spec 004's
    derive_duration_minutes only because the recovery after the LAST rep is present."""
    steps = [
        RepeatGroup(
            repeat=4,
            steps=[
                Step(kind="work", duration_minutes=3, zone_code="Z4"),
                Step(kind="recovery", duration_minutes=3, zone_code="ZR"),
            ],
        )
    ]
    rendered = render_dsl(steps, _R3_ZONES)
    # One work + one recovery line under the single 4x — not 4 work lines, not 3 recoveries.
    assert rendered == "Main set\n4x\n- 3m 88-94%\n- 3m 50%"


def test_steady_step_is_a_headerless_line():
    steps = [Step(kind="steady", duration_minutes=90, zone_code="ZW")]
    assert render_dsl(steps, _R3_ZONES) == "- 90m 50-65%"


def test_empty_session_is_refused_not_rendered_empty():
    with pytest.raises(EmptySessionError):
        render_dsl([], _R3_ZONES)


def test_unknown_zone_raises_rather_than_guessing():
    steps = [Step(kind="steady", duration_minutes=60, zone_code="Z9")]
    with pytest.raises(ValueError, match="Z9"):
        render_dsl(steps, _R3_ZONES)
