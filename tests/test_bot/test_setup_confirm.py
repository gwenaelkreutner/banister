"""First-run setup: confirmation screen + profile building (spec 007 US1, FR-005/FR-010).

The FSM wiring is exercised live (quickstart Scenario 1); these tests pin the pure
helpers — the screen renderer, the FSM-flatten, and `_build_profile` reading confirmed
source values.
"""
from __future__ import annotations

from datetime import date

from app.bot.routers.setup import (
    _build_profile,
    _built_from_recap,
    _read_profile_to_fsm,
    _render_confirm_screen,
)
from app.providers.intervals.athlete_profile import map_athlete_profile

_ATHLETE = {
    "id": "iX",
    "sex": "M",
    "locale": "fr",
    "icu_weight": 67.0,
    "icu_resting_hr": 65,
    "icu_date_of_birth": "1990-01-01",
    "sportSettings": [
        {"types": ["Ride", "VirtualRide"], "ftp": 290, "lthr": 182, "max_hr": 202},
    ],
}


def test_confirm_screen_shows_every_value_with_its_origin():
    rp = map_athlete_profile(_ATHLETE)
    screen = _render_confirm_screen(rp)
    assert "290" in screen and "Réglages sport" in screen        # FTP + origin
    assert "202" in screen and "182" in screen                    # HR figures
    assert "intervals.icu" in screen
    assert "mesurée" in screen                                    # the RHR caveat (FR-005)
    assert "puissance" in screen                                  # coaching mode stated


def test_no_cycling_ftp_screen_says_hr_mode():
    payload = {**_ATHLETE, "sportSettings": [{"types": ["Ride"], "lthr": 180, "max_hr": 195}]}
    rp = map_athlete_profile(payload)
    screen = _render_confirm_screen(rp)
    assert "fréquence cardiaque" in screen
    assert "absent" in screen  # FTP row shows absent, not a default


def test_build_profile_marks_source_values_and_never_asks_age():
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "event", "target_date": "2026-12-01", "hours_per_week": 8,
                "health_constraints": False})

    profile = _build_profile(fsm)
    assert profile.equipment.ftp == 290
    assert profile.equipment.ftp_source == "source"
    assert profile.physio.hr_max == 202
    assert profile.physio.hr_max_source == "source"
    assert profile.physio.hr_rest == 65
    assert profile.physio.hr_rest_source == "source"
    # age came from DOB, not a question
    dob = date(1998, 6, 29)
    today = date.today()
    assert profile.physio.age == today.year - dob.year - (
        (today.month, today.day) < (dob.month, dob.day)
    )
    assert profile.coaching_mode == "power"


def test_build_profile_keeps_the_source_value_when_the_athlete_corrected_it():
    """FR-007 — a correction is kept at the source's current value, not the wanted one."""
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({
        "goal": "event", "hours_per_week": 8, "health_constraints": False,
        "corrections_deferred": {"ftp": {"wanted": 305.0, "current": 290}},
    })
    profile = _build_profile(fsm)
    assert profile.equipment.ftp == 290  # NOT 305 (FR-007)

    recap = _built_from_recap(profile, fsm, seeded=False)
    assert "305" in recap and "290" in recap and "intervals.icu" in recap


def test_missing_source_ftp_falls_back_to_hr_mode_no_silent_default():
    payload = {**_ATHLETE, "sportSettings": [{"types": ["Ride"], "lthr": 180, "max_hr": 195}]}
    rp = map_athlete_profile(payload)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "fitness", "hours_per_week": 6, "health_constraints": False})
    profile = _build_profile(fsm)
    assert profile.coaching_mode == "hr"
    assert profile.equipment.power_meter is False


def test_seeded_fitness_is_disclosed_in_the_recap():
    rp = map_athlete_profile(_ATHLETE)
    fsm = _read_profile_to_fsm(rp)
    fsm.update({"goal": "fitness", "hours_per_week": 6, "health_constraints": False})
    profile = _build_profile(fsm)
    recap = _built_from_recap(profile, fsm, seeded=True)
    assert "estimation prudente" in recap  # FR-010
