"""The FTP follows intervals.icu through new rides (found live 2026-11-23: FTP set to
270 W on intervals.icu, the coach still said 290 W). `icu_ftp` is the settings FTP that
intervals.icu copies onto each activity; old activities keep the FTP of their day."""
from __future__ import annotations

from app.services.athlete_setup import ftp_from_activities


def _ride(day, ftp, type_="Ride", **extra):
    return {"type": type_, "start_date": f"2026-11-{day:02d}T09:00:00Z", "icu_ftp": ftp,
            "icu_rolling_ftp": 268, "icu_pm_ftp": 221, **extra}


def test_the_newest_ride_gives_the_ftp():
    assert ftp_from_activities([_ride(15, 290), _ride(21, 270), _ride(18, 270)]) == 270


def test_an_older_ride_does_not_bring_back_the_old_ftp():
    assert ftp_from_activities([_ride(21, 270), _ride(15, 290)]) == 270


def test_estimates_are_never_taken_for_the_ftp():
    assert ftp_from_activities([_ride(21, None)]) is None


def test_a_non_cycling_activity_is_ignored():
    """A run carries the running FTP, not the cycling one."""
    assert ftp_from_activities([_ride(15, 290), _ride(21, 400, type_="Run")]) == 290


def test_virtual_rides_count():
    assert ftp_from_activities([_ride(21, 270, type_="VirtualRide")]) == 270


def test_no_ride_no_ftp():
    assert ftp_from_activities([]) is None
