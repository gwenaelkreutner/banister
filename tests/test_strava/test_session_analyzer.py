from app.strava.analysis_models import RawActivity, RawActivityStreams
from app.strava.analyzer import SessionAnalyzer

# ── Helpers ────────────────────────────────────────────────────────────────

_PHYSIO = dict(hr_max=190, hr_rest=50, threshold_hr=170)


def test_analyze_no_longer_computes_tss_or_zones_locally():
    """spec 002 FR-017: tss and time_in_zones_s are no longer derived here — that is
    intervals.icu's job now (app/providers/intervals/mapper.py). This is the reduced
    Strava path's behavior during the migration window, not a bug."""
    raw = RawActivity(
        activity_id="42",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        moving_time_s=3500,
        distance_m=32000,
        avg_power=210,
        weighted_avg_power=250,
        max_power=600,
        avg_hr=150,
        max_hr=178,
        avg_speed_m_s=8.9,
        has_power=True,
        has_heartrate=True,
        has_gps=True,
        streams=RawActivityStreams(
            time=[0, 60, 120, 180, 240, 300],
            watts=[120, 140, 150, 160, 170, 180],
            heartrate=[120, 130, 135, 140, 145, 148],
        ),
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250, hr_max=190, hr_rest=50, threshold_hr=170)

    assert analyzed.session_id == "42"
    assert analyzed.tss is None
    assert analyzed.time_in_zones_s == {}
    assert analyzed.dominant_zone is None
    assert analyzed.cardiac_drift_index is None


def test_analyze_without_streams_leaves_empty_zones():
    raw = RawActivity(
        activity_id="11",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=1800,
        moving_time_s=1750,
        has_power=False,
        has_heartrate=False,
        has_gps=False,
    )

    analyzed = SessionAnalyzer().analyze(raw)

    assert analyzed.time_in_zones_s == {}
    assert analyzed.dominant_zone is None


def test_respect_zones_score_is_none_without_zone_data():
    """No local zone derivation anymore (T015) -> respect_zones_score must read as
    'unknown', not as a real 0% compliance figure (FR-020: null is not zero)."""
    raw = RawActivity(
        activity_id="99",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=300,
        moving_time_s=300,
        has_power=True,
        has_heartrate=False,
        has_gps=True,
        streams=RawActivityStreams(
            time=[0, 60, 120, 180, 240, 300],
            watts=[120, 130, 140, 155, 165, 175],
        ),
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=200, planned_zone="Z2", planned_target_time_in_zone_s=120)

    assert analyzed.respect_zones_score is None


def test_analyze_uses_strava_normalized_power_when_available():
    raw = RawActivity(
        activity_id="101",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        moving_time_s=3500,
        avg_power=200,
        weighted_avg_power=235,
        has_power=True,
        has_heartrate=False,
        has_gps=True,
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250)

    assert analyzed.normalized_power == 235
    assert analyzed.normalized_power_source == "strava"


def test_analyze_without_weighted_power_and_streams_leaves_normalized_power_empty():
    raw = RawActivity(
        activity_id="102",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        moving_time_s=3500,
        avg_power=180,
        has_power=True,
        has_heartrate=False,
        has_gps=True,
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250)

    assert analyzed.normalized_power is None
    assert analyzed.normalized_power_source is None


def test_variability_index_computed_from_np_and_avg_power():
    """VI = NP / avg_power — calculé depuis weighted_avg_power Strava. This formula
    itself is not what spec 002 removes (it stays, verified numerically identical to
    intervals.icu's own icu_variability_index — research R9a); what's removed is the
    stream-based local computation of NP that used to feed it."""
    raw = RawActivity(
        activity_id="vi_1",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        moving_time_s=3500,
        avg_power=200,
        weighted_avg_power=240,  # NP Strava
        has_power=True,
        has_heartrate=False,
        has_gps=True,
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250)

    # VI = 240 / 200 = 1.2
    assert analyzed.variability_index is not None
    assert abs(analyzed.variability_index - 1.2) < 0.01


def test_variability_index_none_when_avg_power_missing():
    """Pas de VI calculable sans puissance moyenne."""
    raw = RawActivity(
        activity_id="vi_2",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        weighted_avg_power=240,
        avg_power=None,
        has_power=True,
        has_heartrate=False,
        has_gps=True,
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250)

    assert analyzed.variability_index is None


def test_variability_index_none_when_no_normalized_power():
    """Pas de NP → pas de VI."""
    raw = RawActivity(
        activity_id="vi_3",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        avg_power=200,
        # ni weighted_avg_power ni streams → NP = None
        has_power=True,
        has_heartrate=False,
        has_gps=True,
    )

    analyzed = SessionAnalyzer().analyze(raw, ftp=250)

    assert analyzed.variability_index is None


def test_fatigue_anomaly_is_always_none():
    """spec 002 FR-017/T018: calc_hrss (the only source of fatigue_anomaly here) is no
    longer called from the live per-activity analysis path — HRSS routing, the sex
    parameter and RPE-vs-cardiac mismatch detection via this path are retired along with
    it. detect_fatigue_anomaly_scalar itself is untouched (app/engine/tss.py) and still
    used directly by the RPE capture flow (app/bot/routers/session_log.py)."""
    raw = RawActivity(
        activity_id="hr_test",
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=3600,
        moving_time_s=3600,
        has_power=False,
        has_heartrate=True,
        has_gps=False,
        streams=RawActivityStreams(
            time=list(range(3601)),
            heartrate=[170.0] * 3601,
        ),
    )

    analyzed = SessionAnalyzer().analyze(raw, user_rpe=10, **_PHYSIO)

    assert analyzed.fatigue_anomaly is None
