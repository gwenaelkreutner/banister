from datetime import date, datetime
from types import SimpleNamespace

from app.engine.schemas import SessionSpec
from app.providers.analysis.analysis_models import AnalyzedSession
from app.providers.analysis.matching import evaluate_activity_plan_match, score_activity_vs_session


def _analyzed(
    duration_s: int = 90 * 60,
    tss: float = 72.0,
    session_type_real: str = "endurance",
    dominant_zone: str | None = "Z2",
    environment: str = "outdoor",
    intensity_factor: float | None = None,
) -> AnalyzedSession:
    return AnalyzedSession(
        session_id="test-1",
        source="intervals_icu",
        sport_type="ride",
        start_datetime=datetime(2026, 1, 6, 10, 0),
        duration_s=duration_s,
        has_power=False,
        has_heartrate=True,
        has_gps=True,
        tss=tss,
        session_type_real=session_type_real,
        dominant_zone=dominant_zone,
        environment=environment,
        intensity_factor=intensity_factor,
    )


def _session(day: int = 1, workout_type: str = "endurance") -> SessionSpec:
    return SessionSpec(
        day_of_week=day,
        workout_type=workout_type,
        zone_code="Z2",
        duration_minutes=90,
        target_time_in_zone_minutes=60,
        tss_target=70,
        description_fr="Sortie endurance",
    )


def _plan(start: date, session: SessionSpec):
    plan_technical = {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": session.tss_target,
                "sessions": [
                    {
                        "day_of_week": session.day_of_week,
                        "workout_type": session.workout_type,
                        "zone_code": session.zone_code,
                        "duration_minutes": session.duration_minutes,
                        "target_time_in_zone_minutes": session.target_time_in_zone_minutes,
                        "tss_target": session.tss_target,
                        "description_fr": session.description_fr,
                    }
                ],
            }
        ],
        "zones": {
            "Z2": {
                "name": "Endurance",
                "code": "Z2",
                "lower_pct": 0.56,
                "upper_pct": 0.75,
                "description_fr": "Endurance aérobie",
            }
        },
        "initial_weekly_tss": 200,
        "peak_weekly_tss": 350,
        "weeks_count": 1,
        "coaching_mode": "power",
    }
    return SimpleNamespace(start_date=start, plan_technical=plan_technical)


def test_score_activity_exact_match():
    """Endurance réalisée → séance endurance : score élevé."""
    session = _session()
    analyzed = _analyzed()

    result = score_activity_vs_session(analyzed, session)

    assert result.match_level == "exact"
    assert result.confidence_score >= 85


def test_score_activity_weak_match_type_mismatch():
    """Recovery courte réalisée → long ride planifié : score faible."""
    session = _session(workout_type="long_ride")
    analyzed = _analyzed(
        duration_s=35 * 60,
        tss=20.0,
        session_type_real="recovery",
        dominant_zone="Z1",
        environment="indoor",
    )

    result = score_activity_vs_session(analyzed, session)

    assert result.match_level in {"weak", "none"}
    assert result.confidence_score < 65


def test_score_null_tss_is_neutral_not_a_crash():
    """spec 002: analyzed.tss can be None (source has no computed load for this
    activity — research R9c, ~15% of real activities). Must score neutrally, not
    raise on `None / float` or `f'{None:.0f}'`."""
    session = _session()
    analyzed = _analyzed(tss=None)

    result = score_activity_vs_session(analyzed, session)

    load_reasons = [r for r in result.reasons if "inconnue" in r]
    assert load_reasons, "Devrait indiquer la charge inconnue"
    assert result.confidence_score >= 0


def test_score_type_unknown_is_neutral():
    """Si session_type_real='unknown', la dimension type est neutre (pas de pénalité)."""
    session = _session(workout_type="intervals")
    analyzed = _analyzed(
        duration_s=60 * 60,
        tss=70.0,
        session_type_real="unknown",
        dominant_zone=None,
    )

    result = score_activity_vs_session(analyzed, session)

    # 12 pts (neutre) pour le type — le score global doit être raisonnable, pas pénalisé
    type_reasons = [r for r in result.reasons if "indéterminé" in r]
    assert type_reasons, "Devrait indiquer le type indéterminé"
    assert result.confidence_score >= 40


def test_semantic_matching_prefers_type_over_proximity():
    """Le matching sémantique choisit la séance qui ressemble à l'activité,
    pas forcément la plus proche temporellement.

    Plan : Samedi (day 5) = long_ride (3h), Dimanche (day 6) = recovery (1h)
    Activité : réalisée le samedi, mais courte (recovery) → doit matcher le dimanche.
    """
    long_ride_spec = {
        "day_of_week": 5,
        "workout_type": "long_ride",
        "zone_code": "Z2",
        "duration_minutes": 180,
        "target_time_in_zone_minutes": 120,
        "tss_target": 130,
        "description_fr": "Longue sortie",
    }
    recovery_spec = {
        "day_of_week": 6,
        "workout_type": "recovery",
        "zone_code": "Z1",
        "duration_minutes": 60,
        "target_time_in_zone_minutes": 50,
        "tss_target": 30,
        "description_fr": "Récupération",
    }
    plan_technical = {
        "weeks": [
            {
                "week_number": 1,
                "phase": "base",
                "is_recovery_week": False,
                "total_tss_target": 160,
                "sessions": [long_ride_spec, recovery_spec],
            }
        ],
        "zones": {
            "Z1": {
                "name": "Récup", "code": "Z1", "lower_pct": 0, "upper_pct": 0.55,
                "description_fr": "Récup",
            },
            "Z2": {
                "name": "Endurance", "code": "Z2", "lower_pct": 0.56, "upper_pct": 0.75,
                "description_fr": "Endurance",
            },
        },
        "initial_weekly_tss": 200,
        "peak_weekly_tss": 350,
        "weeks_count": 1,
        "coaching_mode": "hr",
    }
    # Plan commence un lundi : lundi = day 0, samedi = day 5, dimanche = day 6
    plan = SimpleNamespace(start_date=date(2026, 1, 5), plan_technical=plan_technical)

    # Activité courte (recovery) réalisée le samedi (day 5 du plan)
    activity_date = date(2026, 1, 10)  # samedi de la semaine 1
    analyzed = _analyzed(
        duration_s=58 * 60,
        tss=28.0,
        session_type_real="recovery",
        dominant_zone="Z1",
        environment="outdoor",
    )

    result = evaluate_activity_plan_match(plan, analyzed, activity_date, tolerance_days=2)

    assert result.candidate is not None
    # Doit matcher avec la recovery (day 6 = dimanche) et non le long_ride (day 5)
    assert result.candidate.session_spec.workout_type == "recovery", (
        f"Attendu 'recovery', obtenu '{result.candidate.session_spec.workout_type}'"
    )


def test_evaluate_activity_with_day_tolerance():
    """Activité décalée de 1 jour : doit quand même matcher si score suffisant."""
    session = _session(day=1)
    plan = _plan(start=date(2026, 1, 5), session=session)  # lundi, day 1 = mardi
    analyzed = _analyzed(duration_s=90 * 60, tss=70.0)

    # Activité le mercredi (J+1 vs mardi prévu, dans la même semaine d'entraînement)
    result = evaluate_activity_plan_match(
        plan=plan,
        analyzed=analyzed,
        activity_date=date(2026, 1, 7),  # mercredi
        tolerance_days=2,
    )

    assert result.candidate is not None
    assert result.candidate.day_shift == 1
    assert result.score is not None


def test_no_cross_week_match():
    """Une activité de la semaine 2 ne doit pas matcher une séance de la semaine 1."""
    session = _session(day=6)  # dimanche de la semaine 1
    plan = _plan(start=date(2026, 1, 5), session=session)
    analyzed = _analyzed(duration_s=90 * 60, tss=70.0)

    # Activité le lundi de la semaine 2 (8 jours après start_date)
    result = evaluate_activity_plan_match(
        plan=plan,
        analyzed=analyzed,
        activity_date=date(2026, 1, 13),  # lundi semaine 2 — aucune séance planifiée
        tolerance_days=2,
    )

    # Pas de match : la séance est en semaine 1, l'activité est en semaine 2
    assert result.candidate is None
