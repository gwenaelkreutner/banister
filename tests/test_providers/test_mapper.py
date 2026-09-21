"""Tests for the intervals.icu -> AnalyzedSession mapper (spec 002 T012, T013).

FR-020 is the point of this file: an omitted source value must map to None, never 0.0 or
False-as-absence. The no-sensor fixture (icu_training_load: null, device_watts: null,
has_heartrate: null, decoupling: null, ...) is the one that actually exercises this —
the populated fixture would let a `0.0` bug hide behind real numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.providers.intervals.mapper import map_activity_to_analyzed_session

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "intervals"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class TestNullVsZero:
    """Every field below has a null in the fixture. Each assertion is `is None`, not
    falsy — `0.0`, `0`, `{}` and `False` would all pass a falsy check and hide the bug
    FR-020 exists to prevent."""

    def test_tss_stays_none_not_zero(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.tss is None

    def test_intensity_factor_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.intensity_factor is None

    def test_normalized_power_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.normalized_power is None

    def test_variability_index_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.variability_index is None

    def test_cardiac_drift_index_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.cardiac_drift_index is None

    def test_efficiency_factor_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.efficiency_factor is None

    def test_hrr_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.hrr is None

    def test_time_in_zones_stays_empty_not_populated(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.time_in_zones_s == {}

    def test_dominant_zone_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.dominant_zone is None

    def test_has_power_is_false_not_true(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.has_power is False

    def test_has_heartrate_is_false_not_true(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.has_heartrate is False

    def test_avg_power_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.avg_power is None

    def test_avg_hr_and_max_hr_stay_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.avg_hr is None
        assert analyzed.max_hr is None

    def test_distance_stays_none(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.distance_m is None

    def test_intervals_consistency_index_stays_none_without_intervals(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.intervals_consistency_index is None

    def test_environment_is_still_derived_indoor_for_virtual_ride(self):
        """Not everything is null on this fixture — `type: VirtualRide` is real data and
        must still be consumed correctly alongside the null fields around it."""
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.environment == "indoor"


class TestPopulatedFixtureConsumesSourceValuesVerbatim:
    """FR-015/FR-016: values the source computed are consumed as-is, not recomputed."""

    def test_tss_equals_icu_training_load(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.tss == payload["icu_training_load"] == 108

    def test_intensity_factor_is_icu_intensity_divided_by_100(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.intensity_factor == payload["icu_intensity"] / 100

    def test_normalized_power_equals_icu_weighted_avg_watts(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.normalized_power == payload["icu_weighted_avg_watts"] == 207

    def test_variability_index_equals_icu_variability_index(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.variability_index == payload["icu_variability_index"]

    def test_cardiac_drift_index_equals_decoupling_converted_to_a_fraction(self):
        """decoupling is a percentage (15.7 == 15.7%); cardiac_drift_index is a signed
        fraction throughout the rest of the codebase (highlight.py's threshold is 0.08).
        Found live: an unconverted value rendered as '+1571%' in a real notification."""
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.cardiac_drift_index == payload["decoupling"] / 100

    def test_efficiency_factor_equals_icu_efficiency_factor(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.efficiency_factor == payload["icu_efficiency_factor"] == 1.656

    def test_hrr_equals_the_nested_hrr_value_not_the_whole_block(self):
        """icu_hrr is an object ({start_bpm, end_bpm, hrr, ...}), not a scalar — only
        its "hrr" field is the value every other consumer of this term means."""
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.hrr == payload["icu_hrr"]["hrr"] == 62

    def test_time_in_zones_matches_icu_zone_times_verbatim(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        expected = {entry["id"]: entry["secs"] for entry in payload["icu_zone_times"]}
        assert analyzed.time_in_zones_s == expected

    def test_dominant_zone_excludes_the_sweet_spot_bucket(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        # Z1 has the most seconds (4791) among real rate zones; "SS" (365s) is a
        # compliance sub-bucket, not a competing rate zone, and must not win.
        assert analyzed.dominant_zone == "Z1"

    def test_environment_is_outdoor_for_a_real_ride(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.environment == "outdoor"

    def test_session_id_is_the_source_activity_id(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.session_id == str(payload["id"])

    def test_source_is_intervals_icu(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_full.json"))
        assert analyzed.source == "intervals_icu"


class TestIntervalsConsistencyIndex:
    """T022: rebuilt on the source's own interval detection (icu_intervals, type ==
    WORK) rather than our own stream-block detection."""

    def test_computed_from_work_interval_wattages(self):
        payload = _load("activity_with_intervals.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.intervals_consistency_index is not None
        assert 0.0 <= analyzed.intervals_consistency_index <= 1.0

    def test_none_when_fewer_than_two_work_intervals(self):
        payload = _load("activity_full.json")  # no icu_intervals fetched at all
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.intervals_consistency_index is None


class TestRespectZonesScore:
    def test_none_without_a_planned_zone(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.respect_zones_score is None

    def test_computed_against_a_planned_zone_and_target(self):
        payload = _load("activity_full.json")
        analyzed = map_activity_to_analyzed_session(
            payload, planned_zone="Z1", planned_target_time_in_zone_s=4000
        )
        assert analyzed.respect_zones_score is not None
        assert 0 <= analyzed.respect_zones_score <= 100

    def test_none_when_the_no_sensor_activity_has_no_zone_data(self):
        payload = _load("activity_no_sensors.json")
        analyzed = map_activity_to_analyzed_session(
            payload, planned_zone="Z2", planned_target_time_in_zone_s=600
        )
        assert analyzed.respect_zones_score is None


class TestThresholdStability:
    """FR-022: per-activity values are written once at ingestion from whatever the
    payload already carries, and never re-derived from a threshold (FTP, hr_max, ...)
    supplied at read time — the mapper takes no such parameter at all. Re-mapping the
    exact same stored payload must always produce the exact same numbers, regardless of
    what the athlete's current thresholds happen to be when it is re-read."""

    def test_remapping_the_same_payload_is_stable(self):
        payload = _load("activity_full.json")
        first = map_activity_to_analyzed_session(payload)
        second = map_activity_to_analyzed_session(payload)

        assert first.tss == second.tss
        assert first.intensity_factor == second.intensity_factor
        assert first.normalized_power == second.normalized_power
        assert first.variability_index == second.variability_index
        assert first.cardiac_drift_index == second.cardiac_drift_index
        assert first.time_in_zones_s == second.time_in_zones_s

    def test_mapper_signature_takes_no_threshold_parameters(self):
        """A threshold parameter (ftp, hr_max, ...) would let a later value retroactively
        change a stored activity's numbers on re-read — exactly what FR-022 forbids.
        Asserting the signature, not just behavior, makes a regression here fail loudly
        rather than only on a coincidental value match."""
        import inspect

        params = set(inspect.signature(map_activity_to_analyzed_session).parameters)
        assert params == {
            "payload",
            "planned_session_id",
            "planned_workout_type",
            "planned_zone",
            "planned_tss",
            "planned_target_time_in_zone_s",
        }


class TestIcuRpeConsumedVerbatim:
    """2026-09-21 — RPE renseigné sur intervals.icu, jamais lu avant ça (trouvé en
    diagnostiquant /review en conditions réelles : l'athlète l'avait saisi côté
    intervals.icu, Banister l'ignorait entièrement). Consommé tel quel (Principe IV,
    échelle standard 1-10) — pas de bucketing ici, voir app/engine/rpe.py pour la
    conversion vers hard/normal/easy (utilisée uniquement à l'affichage)."""

    def test_mapper_passes_icu_rpe_through_verbatim(self):
        payload = dict(_load("activity_full.json"))
        payload["icu_rpe"] = 9
        analyzed = map_activity_to_analyzed_session(payload)
        assert analyzed.rpe == 9

    def test_mapper_leaves_rpe_none_when_source_has_no_value(self):
        analyzed = map_activity_to_analyzed_session(_load("activity_no_sensors.json"))
        assert analyzed.rpe is None
