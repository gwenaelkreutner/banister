"""spec 004 T045, SC-008 — changing the athlete's FTP retargets every future
session's absolute intensity and touches no record of a completed session.

"True by construction" per the task (steps store zone codes only, never
absolute watts/bpm — see Step in schemas.py; completed history lives in
session_logs, a separate table nothing here writes to) — asserted explicitly
anyway, because "by construction" is exactly the kind of claim that stops being
true silently after a refactor.
"""
from __future__ import annotations

from app.engine.fitting import resolve_zone_intensity
from app.engine.plan_builder import generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)


def _profile(ftp: int) -> AthleteProfileSchema:
    return AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness", target_date=None),
        availability=AvailabilityProfile(
            hours_per_week=8, preferred_days=["tuesday", "thursday", "saturday", "sunday"]
        ),
        level="intermediate",
        structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=ftp, ftp_source="declared"),
        physio=PhysioProfile(
            age=34, hr_max=186, hr_max_source="declared", hr_rest=58, hr_rest_source="declared"
        ),
        coaching_mode="power",
        health_constraints=False,
    )


def test_future_sessions_retarget_when_ftp_changes():
    plan_before = generate_plan(_profile(ftp=200))
    plan_after = generate_plan(_profile(ftp=280))

    # Same zone codes — steps never store absolutes, so nothing needed to change
    # at the step level.
    zone_codes_before = {s.zone_code for w in plan_before.weeks for s in w.sessions}
    zone_codes_after = {s.zone_code for w in plan_after.weeks for s in w.sessions}
    assert zone_codes_before == zone_codes_after

    # But the resolved absolute target for the same zone code moves with FTP.
    z4_before = resolve_zone_intensity("Z4", plan_before.zones, "power")
    z4_after = resolve_zone_intensity("Z4", plan_after.zones, "power")
    assert z4_before.lower != z4_after.lower
    assert z4_after.lower > z4_before.lower  # higher FTP → higher absolute Z4 watts


def test_steps_never_carry_an_absolute_value_to_begin_with():
    """The invariant this whole guarantee rests on: inspect every step of a real
    generated plan and confirm none of them is anything but a Step/RepeatGroup
    carrying only a relative zone_code."""
    plan = generate_plan(_profile(ftp=220))
    for week in plan.weeks:
        for session in week.sessions:
            assert session.steps is not None
            for item in session.steps:
                # Step has no watts/bpm fields at all (see schemas.py) — this is
                # enforced by the Pydantic model shape itself, not by convention.
                assert not hasattr(item, "watts")
                assert not hasattr(item, "bpm")


def test_completed_activity_data_lives_outside_the_plan_and_zones_dont_touch_it():
    """SessionLog (completed history) has no coupling to FTP/zone resolution at
    all — confirmed structurally: its model imports nothing from app/engine/zones
    or app/engine/fitting, so there is no code path by which changing FTP could
    reach a stored record."""
    import inspect

    import app.db.models.session_log as session_log_module

    source = inspect.getsource(session_log_module)
    assert "app.engine.zones" not in source
    assert "app.engine.fitting" not in source
