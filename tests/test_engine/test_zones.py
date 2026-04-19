import pytest
from app.engine.zones import compute_power_zones, compute_hr_zones


def test_power_zones_count():
    zones = compute_power_zones(ftp=200)
    assert len(zones) == 6


def test_power_zones_z2_range():
    zones = compute_power_zones(ftp=200)
    z2 = zones["Z2"]
    assert z2.lower_watts == int(200 * 0.55)
    assert z2.upper_watts == int(200 * 0.75)


def test_power_zones_z4_range():
    zones = compute_power_zones(ftp=300)
    z4 = zones["Z4"]
    assert z4.lower_watts == int(300 * 0.87)
    assert z4.upper_watts == int(300 * 0.95)


def test_power_zones_all_have_description():
    zones = compute_power_zones(ftp=250)
    for code, zone in zones.items():
        assert zone.description_fr, f"Zone {code} manque une description"


def test_hr_zones_count():
    zones = compute_hr_zones(hr_max=185, hr_rest=55)
    assert len(zones) == 6


def test_hr_zones_bpm_in_range():
    zones = compute_hr_zones(hr_max=185, hr_rest=55)
    for code, zone in zones.items():
        assert zone.lower_bpm is not None
        assert zone.upper_bpm is not None
        assert zone.lower_bpm >= 55, f"{code}: lower_bpm trop bas"
        assert zone.upper_bpm <= 185 + 5, f"{code}: upper_bpm trop haut"


def test_hr_zones_monotonically_increasing():
    zones = compute_hr_zones(hr_max=185, hr_rest=55)
    codes = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"]
    prev_upper = 0
    for code in codes:
        zone = zones[code]
        assert zone.lower_bpm >= prev_upper - 1  # tolérance 1 bpm
        prev_upper = zone.upper_bpm
