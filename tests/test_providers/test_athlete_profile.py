"""Reading the athlete profile from GET /athlete (spec 007 US1, FR-001/FR-005/FR-008/FR-009).

`map_athlete_profile` is the pure, testable half. The fixture is a real (owner-owned)
`GET /athlete` payload trimmed to the fields the reader touches.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.providers.intervals.athlete_profile import map_athlete_profile

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "intervals" / "athlete_profile.json"
_ATHLETE = json.loads(_FIX.read_text(encoding="utf-8"))


def test_thresholds_come_from_the_cycling_sport_settings_entry():
    p = map_athlete_profile(_ATHLETE)
    # This account's cycling entry: ftp 290, lthr 182, max_hr 202.
    assert p.ftp.present and p.ftp.value == 290
    assert p.lthr.value == 182
    assert p.max_hr.value == 202
    assert p.has_power_meter is True
    assert p.coaching_mode == "power"
    assert "Réglages sport" in p.ftp.origin


def test_age_is_derived_from_date_of_birth():
    p = map_athlete_profile(_ATHLETE)
    assert p.date_of_birth.value == "1990-01-01"
    # age = years since DOB, adjusted for whether the birthday has passed this year
    dob = date(1990, 1, 1)
    today = date.today()
    expected = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    assert p.age.value == expected
    assert "naissance" in p.age.origin


def test_top_level_fields_carry_their_origin():
    p = map_athlete_profile(_ATHLETE)
    assert p.weight_kg.value == 67.0
    assert p.sex.value == "M"
    assert p.locale.value == "fr"
    assert all("intervals.icu" in rv.origin for rv in (p.weight_kg, p.sex))


def test_resting_hr_is_flagged_as_a_profile_default():
    """research R1 — icu_resting_hr is a profile field, not the measured series."""
    p = map_athlete_profile(_ATHLETE)
    assert p.resting_hr.present
    assert p.resting_hr.note and "mesurée" in p.resting_hr.note


def test_no_cycling_ftp_means_hr_mode_and_an_absent_ftp(monkeypatch):
    """FR-009 — an athlete whose cycling sport has no FTP proceeds on heart rate."""
    payload = json.loads(_FIX.read_text(encoding="utf-8"))
    for s in payload["sportSettings"]:
        s["ftp"] = None
    p = map_athlete_profile(payload)
    assert not p.ftp.present            # absent, not defaulted (FR-008)
    assert p.has_power_meter is False
    assert p.coaching_mode == "hr"
    assert p.lthr.value == 182          # HR figures still read


def test_a_missing_field_is_absent_never_defaulted():
    p = map_athlete_profile({"id": "iX", "sportSettings": []})
    for rv in (p.ftp, p.lthr, p.max_hr, p.weight_kg, p.sex, p.age):
        assert not rv.present           # FR-008: no silent default
