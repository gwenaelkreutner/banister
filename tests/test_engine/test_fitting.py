"""spec 004 T044 — fitting preserves structural character within ScalingRules
bounds, refuses (never silently clamps) when a target can't be met, and zone
resolution follows the athlete's own terms (power/HR) without fabricating
absolutes for an undefined zone.
"""
from __future__ import annotations

import pytest

from app.engine.fitting import (
    FittingError,
    fit_template,
    resolve_zone_intensity,
)
from app.engine.session_library import load_library
from app.engine.zones import compute_hr_zones, compute_power_zones


def _template(template_id: str):
    return next(t for t in load_library() if t.id == template_id)


class TestIntervalFitting:
    def test_fits_within_scaling_bounds_and_hits_target_closely(self):
        template = _template("threshold-3x12")
        result = fit_template(template, target_tss=100.0, coaching_mode="power", ftp=220)
        assert abs(result.tss_target - 100.0) <= 100.0 * 0.15
        assert result.zone_code == "Z4"

    def test_preserves_repeat_group_structure(self):
        """The fitted result is still a real structured session — steps, not a
        flat number — and its own duration must agree with its own steps (the
        Pydantic validator would catch this if fitting produced something
        inconsistent, but assert it explicitly here too)."""
        from app.engine.schemas import RepeatGroup, derive_duration_minutes

        template = _template("vo2-5x5")
        result = fit_template(template, target_tss=80.0, coaching_mode="power", ftp=220)
        assert any(isinstance(item, RepeatGroup) for item in result.steps)
        assert derive_duration_minutes(result.steps) == result.duration_minutes

    def test_refuses_when_target_is_unreachable_within_bounds(self):
        template = _template("threshold-3x12")
        with pytest.raises(FittingError, match="threshold-3x12.*scaling bounds"):
            fit_template(template, target_tss=100000.0, coaching_mode="power", ftp=220)

    def test_refusal_names_the_closest_achievable_value(self):
        """FR-024: refusal must name the failed constraint, not just say 'no'."""
        template = _template("threshold-3x12")
        with pytest.raises(FittingError, match="closest achievable"):
            fit_template(template, target_tss=100000.0, coaching_mode="power", ftp=220)


class TestSteadyFitting:
    def test_fits_endurance_to_a_target_duration(self):
        template = _template("endurance-z2")
        result = fit_template(template, target_tss=150.0, coaching_mode="power", ftp=220)
        assert result.duration_minutes > 0
        assert abs(result.tss_target - 150.0) <= 150.0 * 0.15

    def test_refuses_when_steady_range_cannot_reach_target(self):
        template = _template("recovery-z1")  # steady_minutes_range: [20, 90]
        with pytest.raises(FittingError, match="recovery-z1"):
            fit_template(template, target_tss=500.0, coaching_mode="power", ftp=220)


class TestAvailabilityRefusal:
    def test_refuses_when_fitted_session_exceeds_available_time(self):
        template = _template("endurance-z2")
        with pytest.raises(FittingError, match="exceeds the athlete's stated availability"):
            fit_template(
                template, target_tss=150.0, coaching_mode="power", ftp=220,
                available_minutes=30,
            )

    def test_never_silently_clamps_to_fit_availability(self):
        """The old plan_builder.py behaviour (before spec 004) silently clamped an
        oversized session to fit — this must refuse instead (research R2, T015)."""
        template = _template("long-ride-z2")
        with pytest.raises(FittingError):
            fit_template(
                template, target_tss=300.0, coaching_mode="power", ftp=220,
                available_minutes=45,
            )


class TestHeartRateMode:
    def test_interval_fitting_works_in_hr_mode(self):
        template = _template("threshold-3x12")
        result = fit_template(template, target_tss=60.0, coaching_mode="hr")
        assert result.duration_minutes > 0

    def test_steady_fitting_works_in_hr_mode(self):
        template = _template("endurance-z2")
        result = fit_template(template, target_tss=80.0, coaching_mode="hr")
        assert result.duration_minutes > 0


class TestZoneResolution:
    def test_power_mode_resolves_to_watts(self):
        zones = compute_power_zones(220)
        resolved = resolve_zone_intensity("Z4", zones, "power")
        assert resolved.unit == "W"
        assert resolved.lower is not None and resolved.upper is not None
        assert resolved.lower < resolved.upper

    def test_hr_mode_resolves_to_bpm(self):
        zones = compute_hr_zones(hr_max=186, hr_rest=58)
        resolved = resolve_zone_intensity("Z4", zones, "hr")
        assert resolved.unit == "bpm"
        assert resolved.lower is not None and resolved.upper is not None

    def test_undefined_zone_resolves_to_absent_absolutes_not_fabricated(self):
        """FR-027, Constitution Principle IV — a zone the athlete's scheme doesn't
        define must never get an invented number. The code itself stays
        presentable; only the absolute bounds go missing."""
        zones = compute_power_zones(220)
        resolved = resolve_zone_intensity("Z9", zones, "power")
        assert resolved.zone_code == "Z9"
        assert resolved.lower is None
        assert resolved.upper is None
