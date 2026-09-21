"""Tests pour app/engine/phase_detection.py — classification diagnostique de phase
(2026-09-21, porté depuis Section11, simplifié).

Vérifie : flux principal (comportemental) seul, recoupement avec le flux secondaire
(plan actif / proximité date cible), cas sans plan (mode libre), désaccord entre flux.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app.engine.phase_detection import detect_training_phase

TODAY = date(2026, 9, 21)


def _log(days_ago: int, tss: float, duration_min: float = 60.0) -> SimpleNamespace:
    return SimpleNamespace(
        logged_date=TODAY - timedelta(days=days_ago),
        tss_actual=tss,
        duration_minutes_actual=duration_min,
        status="done",
    )


def _history_weeks(weekly_tss: float, *, sessions_per_week: int = 5, weeks: int = 6) -> list:
    """6 semaines précédentes (non chevauchantes avec les 7 derniers jours, même
    fenêtrage que compute_weekly_snapshot) à `weekly_tss` chacune, en répartissant
    `sessions_per_week` séances égales par semaine (jamais hors-seuil "dur" — 80min pour
    rester sous 70 TSS/h)."""
    per_session = weekly_tss / sessions_per_week
    logs = []
    for w in range(1, weeks + 1):
        for s in range(sessions_per_week):
            logs.append(_log(days_ago=7 * w + s, tss=per_session, duration_min=80.0))
    return logs


def test_no_logs_returns_none():
    assert detect_training_phase([], today=TODAY) is None


def test_no_training_last_7d_defaults_to_base_low_confidence():
    # Charge seulement dans les semaines précédentes, rien sur les 7 derniers jours.
    logs = [_log(days_ago=20, tss=200)]
    result = detect_training_phase(logs, today=TODAY)
    assert result is not None
    assert result.detected_phase == "base"
    assert result.confidence == "low"
    assert "no_training_last_7d" in result.reason_codes


def test_sharp_load_drop_with_a_hard_effort_detects_taper():
    # 6 semaines précédentes à 400 TSS/sem, semaine courante réduite à une seule séance
    # dure — chute nette de charge avec intensité maintenue, signature d'affûtage.
    logs = _history_weeks(400.0)
    logs.append(_log(days_ago=0, tss=90, duration_min=60))  # 90 TSS/h — dur
    result = detect_training_phase(logs, today=TODAY)
    assert result is not None
    assert result.detected_phase == "taper"
    assert result.confidence == "medium"


def test_rising_load_detects_build():
    # 6 semaines précédentes légères, semaine courante nettement plus chargée.
    logs = _history_weeks(150.0)
    logs += [_log(days_ago=d, tss=80) for d in range(0, 7)]
    result = detect_training_phase(logs, today=TODAY)
    assert result is not None
    assert result.detected_phase == "build"


def test_stable_load_with_multiple_hard_days_detects_peak():
    # Charge globale proche de l'historique (pas de tendance forte), mais plusieurs
    # jours durs cette semaine — signature d'une semaine de pic, intensité maintenue.
    logs = _history_weeks(400.0)
    logs.append(_log(days_ago=0, tss=90, duration_min=60))   # dur aujourd'hui
    logs.append(_log(days_ago=1, tss=90, duration_min=60))   # dur hier
    logs += [_log(days_ago=d, tss=80, duration_min=80.0) for d in range(2, 5)]
    result = detect_training_phase(logs, today=TODAY)
    assert result is not None
    assert result.detected_phase == "peak"


def test_stable_load_no_strong_signal_defaults_to_base():
    logs = _history_weeks(400.0)
    logs += [_log(days_ago=d, tss=80, duration_min=80.0) for d in range(0, 5)]
    result = detect_training_phase(logs, today=TODAY)
    assert result is not None
    assert result.detected_phase == "base"
    assert result.confidence == "low"


# ── Flux secondaire — plan actif ─────────────────────────────────────────────


def test_secondary_phase_from_plan_is_used_when_available():
    logs = [_log(days_ago=7 + 7 * w, tss=30) for w in range(6)]
    logs += [_log(days_ago=d, tss=80) for d in range(0, 7)]
    result = detect_training_phase(logs, today=TODAY, plan_week_phase="build")
    assert result is not None
    assert result.secondary_phase == "build"


def test_agreeing_streams_upgrade_confidence_to_high():
    logs = [_log(days_ago=7 + 7 * w, tss=30) for w in range(6)]
    logs += [_log(days_ago=d, tss=80) for d in range(0, 7)]
    result = detect_training_phase(logs, today=TODAY, plan_week_phase="build")
    assert result.detected_phase == "build" and result.secondary_phase == "build"
    assert result.streams_agree is True
    assert result.confidence == "high"


def test_disagreeing_streams_are_stated_not_hidden():
    logs = [_log(days_ago=7 + 7 * w, tss=30) for w in range(6)]
    logs += [_log(days_ago=d, tss=80) for d in range(0, 7)]  # comportement -> build
    result = detect_training_phase(logs, today=TODAY, plan_week_phase="taper")
    assert result.detected_phase == "build"
    assert result.secondary_phase == "taper"
    assert result.streams_agree is False
    assert result.confidence == "medium"  # pas upgradée en désaccord


def test_invalid_plan_phase_string_is_ignored():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(logs, today=TODAY, plan_week_phase="not_a_real_phase")
    assert result.secondary_phase is None
    assert result.streams_agree is None


# ── Flux secondaire — proximité date cible (mode libre, pas de plan) ────────


def test_target_date_within_10_days_suggests_taper():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(
        logs, today=TODAY, target_date=TODAY + timedelta(days=5)
    )
    assert result.secondary_phase == "taper"


def test_target_date_far_away_suggests_base():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(
        logs, today=TODAY, target_date=TODAY + timedelta(days=120)
    )
    assert result.secondary_phase == "base"


def test_target_date_in_the_past_is_ignored():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(
        logs, today=TODAY, target_date=TODAY - timedelta(days=1)
    )
    assert result.secondary_phase is None


def test_plan_phase_takes_priority_over_target_date_when_both_given():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(
        logs, today=TODAY, plan_week_phase="build", target_date=TODAY + timedelta(days=5)
    )
    assert result.secondary_phase == "build"  # pas "taper" (qu'aurait donné la date seule)


def test_no_secondary_signal_at_all_leaves_it_none():
    logs = [_log(days_ago=d, tss=50) for d in range(0, 7)]
    result = detect_training_phase(logs, today=TODAY)
    assert result.secondary_phase is None
    assert result.streams_agree is None
