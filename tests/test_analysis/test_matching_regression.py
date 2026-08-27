from datetime import datetime

from app.engine.schemas import SessionSpec
from app.providers.analysis.analysis_models import AnalyzedSession
from app.providers.analysis.matching import score_activity_vs_session


def _analyzed(duration_s: int, tss: float, session_type_real: str = "endurance") -> AnalyzedSession:
    return AnalyzedSession(
        session_id="reg-1",
        source="intervals_icu",
        sport_type="ride",
        start_datetime=datetime(2026, 1, 6, 10, 0),
        duration_s=duration_s,
        has_power=False,
        has_heartrate=True,
        has_gps=True,
        tss=tss,
        session_type_real=session_type_real,
        dominant_zone="Z2",
        environment="outdoor",
    )


def test_score_activity_exact_match_regression():
    """Régression : endurance 90min TSS≈72 → exact match (≥85)."""
    session = SessionSpec(
        day_of_week=1,
        workout_type="endurance",
        zone_code="Z2",
        duration_minutes=90,
        target_time_in_zone_minutes=60,
        tss_target=70,
        description_fr="Sortie endurance",
    )
    analyzed = _analyzed(duration_s=90 * 60, tss=72.0)

    result = score_activity_vs_session(analyzed, session)

    assert result.match_level == "exact"
    assert result.confidence_score >= 85
