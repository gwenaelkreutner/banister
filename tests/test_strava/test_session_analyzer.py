from app.strava.analysis_models import RawActivity, RawActivityStreams
from app.strava.analyzer import SessionAnalyzer

# ── Helpers ────────────────────────────────────────────────────────────────

_PHYSIO = dict(hr_max=190, hr_rest=50, threshold_hr=170)


def _hr_stream_raw(hr_bpm: float, duration_s: int, activity_id: str = "hr_test") -> RawActivity:
    """RawActivity avec streams HR uniquement (pas de puissance)."""
    t = list(range(duration_s + 1))
    hr = [hr_bpm] * (duration_s + 1)
    return RawActivity(
        activity_id=activity_id,
        sport_type="ride",
        start_datetime="2026-01-01T08:00:00+00:00",
        duration_s=duration_s,
        moving_time_s=duration_s,
        has_power=False,
        has_heartrate=True,
        has_gps=False,
        streams=RawActivityStreams(time=t, heartrate=hr),
    )


def test_analyze_maps_raw_and_computes_tss():
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
    assert analyzed.tss > 0
    assert analyzed.intensity_factor is not None
    assert analyzed.time_in_zones_s


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


def test_analyze_computes_respect_zones_score_from_planned_zone():
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

    assert analyzed.respect_zones_score is not None
    assert 0 <= analyzed.respect_zones_score <= 100


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
    """VI = NP / avg_power — calculé depuis weighted_avg_power Strava."""
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


# ── Tests HRSS (intégration SessionAnalyzer) ──────────────────────────────

import pytest  # noqa: E402 — import groupé en fin de section


class TestHRSSIntegration:
    """Vérifie que SessionAnalyzer route correctement vers HRSS quand disponible."""

    def test_hrss_used_when_hr_streams_and_no_power(self):
        """Streams HR + physio disponibles, pas de puissance → HRSS > 0."""
        raw = _hr_stream_raw(hr_bpm=170, duration_s=3600)
        analyzed = SessionAnalyzer().analyze(raw, **_PHYSIO)

        assert analyzed.tss > 0
        # 1h au LTHR → HRSS ≈ 100
        assert analyzed.tss == pytest.approx(100.0, abs=1.0)

    def test_power_tss_takes_priority_over_hrss(self):
        """NP + FTP disponibles → TSS depuis la puissance, pas HRSS."""
        raw = RawActivity(
            activity_id="prio_pwr",
            sport_type="ride",
            start_datetime="2026-01-01T08:00:00+00:00",
            duration_s=3600,
            moving_time_s=3600,
            avg_power=200,
            weighted_avg_power=230,  # NP Strava
            has_power=True,
            has_heartrate=True,
            has_gps=False,
            streams=RawActivityStreams(
                time=list(range(3601)),
                heartrate=[170.0] * 3601,  # LTHR constant
            ),
        )
        analyzed_power = SessionAnalyzer().analyze(raw, ftp=250, **_PHYSIO)
        analyzed_hrss_only = SessionAnalyzer().analyze(
            _hr_stream_raw(170, 3600, "hrss_ref"), **_PHYSIO
        )

        # TSS power (NP=230, FTP=250) ≈ 34.7, HRSS ≈ 100 — clairement différents
        assert analyzed_power.tss != pytest.approx(analyzed_hrss_only.tss, abs=5.0)

    def test_fallback_when_no_hr_streams(self):
        """Pas de streams HR → fallback calc_tss (avg_hr scalaire)."""
        raw = RawActivity(
            activity_id="no_streams",
            sport_type="ride",
            start_datetime="2026-01-01T08:00:00+00:00",
            duration_s=3600,
            moving_time_s=3600,
            avg_hr=160.0,
            has_power=False,
            has_heartrate=True,
            has_gps=False,
        )
        analyzed = SessionAnalyzer().analyze(raw, **_PHYSIO)
        assert analyzed.tss > 0

    def test_fallback_when_physio_incomplete(self):
        """HR streams présents mais hr_rest manquant → fallback sans HRSS."""
        t = list(range(3601))
        hr = [170.0] * 3601
        raw = RawActivity(
            activity_id="no_physio",
            sport_type="ride",
            start_datetime="2026-01-01T08:00:00+00:00",
            duration_s=3600,
            moving_time_s=3600,
            avg_hr=170.0,
            has_power=False,
            has_heartrate=True,
            has_gps=False,
            streams=RawActivityStreams(time=t, heartrate=hr),
        )
        # hr_rest manquant → condition HRSS non satisfaite → fallback
        analyzed = SessionAnalyzer().analyze(raw, hr_max=190, threshold_hr=170)
        assert analyzed.tss > 0  # fallback fonctionne

    def test_no_fatigue_anomaly_by_default(self):
        """Sans user_rpe → fatigue_anomaly est None."""
        raw = _hr_stream_raw(hr_bpm=140, duration_s=3600)
        analyzed = SessionAnalyzer().analyze(raw, **_PHYSIO)
        assert analyzed.fatigue_anomaly is None

    def test_fatigue_anomaly_triggered_by_high_rpe(self):
        """
        Z2 cardiaque (140 bpm → RPE estimé ≈ 6.4) + RPE déclaré 10
        → FatigueAnomaly sérialisée en dict dans analyzed.fatigue_anomaly.
        """
        raw = _hr_stream_raw(hr_bpm=140, duration_s=3600)
        analyzed = SessionAnalyzer().analyze(raw, user_rpe=10, **_PHYSIO)

        assert analyzed.fatigue_anomaly is not None
        fa = analyzed.fatigue_anomaly
        assert isinstance(fa, dict)
        assert fa["rpe_declared"] == 10
        assert fa["rpe_delta"] >= 3.0
        assert 1.10 <= fa["rpe_factor"] <= 1.20
        assert "message" in fa

    def test_fatigue_anomaly_increases_hrss(self):
        """HRSS avec anomalie RPE > HRSS sans RPE (multiplicateur appliqué)."""
        r_no_rpe = SessionAnalyzer().analyze(_hr_stream_raw(140, 3600, "no_rpe"), **_PHYSIO)
        r_rpe10 = SessionAnalyzer().analyze(
            _hr_stream_raw(140, 3600, "rpe10"), user_rpe=10, **_PHYSIO
        )
        assert r_rpe10.tss > r_no_rpe.tss

    def test_sex_param_passed_through(self):
        """sex='F' produit un HRSS différent de sex='M' (k différents)."""
        # À Z5 supra-LTHR, k_M > k_F → HRSS_M > HRSS_F
        r_m = SessionAnalyzer().analyze(_hr_stream_raw(180, 3600, "sex_m"), sex="M", **_PHYSIO)
        r_f = SessionAnalyzer().analyze(_hr_stream_raw(180, 3600, "sex_f"), sex="F", **_PHYSIO)
        assert r_m.tss > r_f.tss
