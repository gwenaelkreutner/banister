import pytest
from app.engine.tss import (
    estimate_session_tss_power,
    estimate_session_tss_hr,
    estimate_session_tss,
    tss_from_weekly_hours,
)


def test_tss_power_z2_1h():
    # Z2, 60 min, FTP=200 → TSS attendu ~42 (IF≈0.65²×100)
    tss = estimate_session_tss_power("Z2", 60, ftp=200)
    assert 35 <= tss <= 55, f"TSS Z2/60min/FTP200 attendu ~42, obtenu {tss}"


def test_tss_power_z4_45min():
    # Z4 effort soutenu sur 45min doit être > Z2/60min
    tss_z4 = estimate_session_tss_power("Z4", 45, ftp=250)
    tss_z2 = estimate_session_tss_power("Z2", 45, ftp=250)
    assert tss_z4 > tss_z2, "TSS Z4 doit être supérieur à TSS Z2 même durée"


def test_tss_hr_z2_1h():
    tss = estimate_session_tss_hr("Z2", 60)
    assert tss == 45.0


def test_tss_hr_proportional_to_duration():
    tss_60 = estimate_session_tss_hr("Z3", 60)
    tss_120 = estimate_session_tss_hr("Z3", 120)
    assert abs(tss_120 - 2 * tss_60) < 0.1


def test_estimate_session_tss_dispatch():
    tss_power = estimate_session_tss("Z2", 60, "power", ftp=200)
    tss_hr = estimate_session_tss("Z2", 60, "hr")
    # Les deux modes doivent donner des valeurs non nulles et proches d'ordre de grandeur
    assert tss_power > 0
    assert tss_hr > 0


def test_tss_from_weekly_hours():
    assert tss_from_weekly_hours(6) == 240.0
    assert tss_from_weekly_hours(0) == 0.0
    assert tss_from_weekly_hours(10) == 400.0
