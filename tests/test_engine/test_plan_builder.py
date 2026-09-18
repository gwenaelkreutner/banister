from datetime import date, timedelta

import pytest
from app.engine.plan_builder import generate_plan
from app.engine.schemas import (
    AthleteProfileSchema,
    ObjectiveProfile,
    AvailabilityProfile,
    EquipmentProfile,
    PhysioProfile,
)


def make_profile(
    goal_type="event",
    target_weeks=12,
    hours=6,
    level="intermediate",
    power_meter=True,
    ftp=210,
    history=True,
) -> AthleteProfileSchema:
    target_date = date.today() + timedelta(weeks=target_weeks)
    return AthleteProfileSchema(
        objective=ObjectiveProfile(type=goal_type, target_date=target_date),
        availability=AvailabilityProfile(
            hours_per_week=hours,
            preferred_days=["tuesday", "thursday", "saturday", "sunday"],
        ),
        level=level,
        structured_plan_history=history,
        equipment=EquipmentProfile(
            power_meter=power_meter,
            ftp=ftp if power_meter else None,
            ftp_source="declared" if power_meter else "estimated",
        ),
        physio=PhysioProfile(
            age=34,
            hr_max=186,
            hr_max_source="declared",
            hr_rest=58,
            hr_rest_source="declared",
        ),
        coaching_mode="power" if power_meter else "hr",
        health_constraints=False,
    )


def test_plan_has_correct_week_count():
    profile = make_profile(target_weeks=12)
    plan = generate_plan(profile)
    # Race Week may be appended depending on the day-of-week gap between plan end
    # and target_date (triggered when gap >= 3 days, which varies Mon-Sun).
    assert plan.weeks_count in (12, 13)
    assert len(plan.weeks) == plan.weeks_count


def test_plan_last_phase_is_taper():
    profile = make_profile(target_weeks=12)
    plan = generate_plan(profile)
    assert plan.weeks[-1].phase == "taper"


def test_plan_has_6_zones_power():
    profile = make_profile(power_meter=True, ftp=210)
    plan = generate_plan(profile)
    assert len(plan.zones) == 6
    assert "Z2" in plan.zones
    assert plan.zones["Z2"].lower_watts is not None


def test_plan_has_6_zones_hr():
    profile = make_profile(power_meter=False)
    plan = generate_plan(profile)
    assert len(plan.zones) == 6
    assert plan.zones["Z2"].lower_bpm is not None


def test_sessions_on_available_days_only():
    """Found 2026-09-18: this test was flaky, not the engine. `make_profile()` anchors
    `target_date` on `date.today() + 12 weeks`, which lands on the same weekday as
    today — so the race week's day-of-week-of-the-race marker session
    (`_build_race_week`, `day_of_week=race_dow`) fell outside `allowed_days` on any day
    the suite happened to run on a Monday/Wednesday/Friday (3 days out of 7), and passed
    on the other 4 purely by luck. That marker is deliberately anchored to the real race
    date regardless of `preferred_days` — you can't move a race to Tuesday because
    that's a training day — so it's the one legitimate exception, not a bug to chase in
    `plan_builder.py`. Verified deterministic post-fix by checking all 7 possible race
    weekdays directly (not just whatever weekday happened to be today when this was
    written)."""
    profile = make_profile()
    plan = generate_plan(profile)
    allowed_days = {1, 3, 5, 6}  # Tue, Thu, Sat, Sun
    for week in plan.weeks:
        for session in week.sessions:
            if "JOUR DE COURSE" in session.description_fr:
                continue  # race-day marker: anchored to the real date, not preferred_days
            assert session.day_of_week in allowed_days, (
                f"Séance sur jour non disponible: {session.day_of_week}"
            )


def test_sessions_on_available_days_only_is_stable_across_every_race_weekday():
    """The version of the test above without the fix would only fail 3 days out of 7 —
    prove the fix holds for all 7, not just whichever weekday happens to be 'today'."""
    allowed_days = {1, 3, 5, 6}
    base = date.today() + timedelta(weeks=12)
    for offset in range(7):
        profile = make_profile()
        profile.objective.target_date = base + timedelta(days=offset)
        plan = generate_plan(profile)
        for week in plan.weeks:
            for session in week.sessions:
                if "JOUR DE COURSE" in session.description_fr:
                    continue
                assert session.day_of_week in allowed_days, (
                    f"race weekday offset={offset}: séance sur jour non disponible "
                    f"{session.day_of_week}"
                )


def test_tss_target_positive_for_all_weeks():
    profile = make_profile()
    plan = generate_plan(profile)
    for week in plan.weeks:
        assert week.total_tss_target > 0
        for session in week.sessions:
            assert session.tss_target > 0
            assert session.duration_minutes >= 20


def test_peak_tss_greater_than_initial():
    profile = make_profile(hours=6)
    plan = generate_plan(profile)
    assert plan.peak_weekly_tss > plan.initial_weekly_tss


def test_no_target_date_uses_12_weeks():
    profile = AthleteProfileSchema(
        objective=ObjectiveProfile(type="fitness", target_date=None),
        availability=AvailabilityProfile(hours_per_week=5, preferred_days=["monday", "wednesday", "friday"]),
        level="beginner",
        structured_plan_history=False,
        equipment=EquipmentProfile(power_meter=False),
        physio=PhysioProfile(age=30, hr_max=190, hr_max_source="estimated", hr_rest=60, hr_rest_source="estimated"),
        coaching_mode="hr",
        health_constraints=False,
    )
    plan = generate_plan(profile)
    assert plan.weeks_count == 12


def test_beginner_peak_tss_below_cap():
    profile = make_profile(level="beginner", hours=3, history=False)
    plan = generate_plan(profile)
    assert plan.peak_weekly_tss <= 350


def test_plan_starts_on_monday():
    profile = make_profile(target_weeks=12)
    plan = generate_plan(profile)
    assert plan.start_date is not None
    assert plan.start_date.weekday() == 0
    assert plan.weeks[0].start_date == plan.start_date


def test_target_time_in_zone_present_and_coherent():
    profile = make_profile(target_weeks=12)
    plan = generate_plan(profile)

    for week in plan.weeks:
        for session in week.sessions:
            assert session.target_time_in_zone_minutes >= 0
            if session.workout_type == "intervals":
                assert session.target_time_in_zone_minutes > 0
                assert session.target_time_in_zone_minutes <= session.duration_minutes
            else:
                assert session.target_time_in_zone_minutes == 0


def test_taper_activation_interval_targets_24_minutes_in_zone_when_present():
    profile = make_profile(target_weeks=12)
    plan = generate_plan(profile)

    # La dernière semaine peut être une Race Week (séance "JOUR DE COURSE").
    # Dans ce cas, la semaine taper normale est l'avant-dernière.
    last_week = plan.weeks[-1]
    is_race_week = any(
        s.workout_type == "long_ride" and "JOUR DE COURSE" in s.description_fr
        for s in last_week.sessions
    )
    taper_week = plan.weeks[-2] if is_race_week and len(plan.weeks) >= 2 else last_week

    activation_sessions = [
        s for s in taper_week.sessions
        if s.workout_type == "intervals" and "3×8min activation" in s.description_fr
    ]

    # Selon la périodisation, la semaine taper peut être en récupération:
    # dans ce cas l'activation 3×8min n'est pas injectée.
    if not activation_sessions:
        assert taper_week.is_recovery_week is True
        return

    assert activation_sessions[0].target_time_in_zone_minutes == 24
