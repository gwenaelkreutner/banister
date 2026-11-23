"""Thresholds live at the source: an FTP changed on intervals.icu after setup must reach
the stored profile (found live 2026-11-23 — the coach still said 290 W after the athlete
set 270 W on intervals.icu)."""
from __future__ import annotations

import json
from pathlib import Path

from app.providers.intervals.athlete_profile import map_athlete_profile
from app.services.athlete_setup import apply_source_thresholds

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "intervals" / "athlete_profile.json"


def _read(**sport):
    payload = json.loads(_FIX.read_text(encoding="utf-8"))
    for s in payload["sportSettings"]:
        s.update(sport)
    return map_athlete_profile(payload)


def _stored(ftp=290):
    return {
        "equipment": {"power_meter": True, "ftp": ftp, "ftp_source": "source"},
        "physio": {"age": 28, "hr_max": 202, "hr_max_source": "source",
                   "hr_rest": 65, "hr_rest_source": "source"},
        "coaching_mode": "power",
        "weight_kg": 67.0,
    }


def test_a_new_ftp_at_the_source_replaces_the_stored_one():
    data, changed = apply_source_thresholds(_stored(290), _read(ftp=270))
    assert data["equipment"]["ftp"] == 270
    assert changed["ftp"] == {"from": 290, "to": 270}


def test_nothing_changes_when_the_source_agrees():
    read = _read()
    stored = _stored(int(read.ftp.value))
    stored["physio"].update(hr_max=int(read.max_hr.value), hr_rest=int(read.resting_hr.value),
                            age=int(read.age.value))
    stored["weight_kg"] = float(read.weight_kg.value)
    _, changed = apply_source_thresholds(stored, read)
    assert changed == {}


def test_a_value_missing_at_the_source_keeps_the_stored_one():
    stored = _stored(250)
    stored["equipment"]["ftp_source"] = "declared"
    data, changed = apply_source_thresholds(stored, _read(ftp=None))
    assert data["equipment"]["ftp"] == 250
    assert data["equipment"]["ftp_source"] == "declared"
    assert data["coaching_mode"] == "power"
    assert "ftp" not in changed


def test_the_stored_profile_is_not_mutated_in_place():
    stored = _stored(290)
    apply_source_thresholds(stored, _read(ftp=270))
    assert stored["equipment"]["ftp"] == 290
