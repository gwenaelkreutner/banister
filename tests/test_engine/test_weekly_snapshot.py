"""
Tests pour app/engine/weekly_snapshot.py.

Vérifie : fenêtres temporelles, formule Foster, monotonie, tendance de charge.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.engine.weekly_snapshot import WeeklySnapshot, compute_weekly_snapshot


def _log(logged_date: date, tss: float, status: str = "done") -> SimpleNamespace:
    """Crée un objet minimal compatible avec compute_weekly_snapshot (duck-typing)."""
    return SimpleNamespace(logged_date=logged_date, tss_actual=tss, status=status)


TODAY = date(2026, 3, 18)


# ── Cas de base ────────────────────────────────────────────────────────────────

def test_empty_logs_returns_zero_snapshot():
    snap = compute_weekly_snapshot([], TODAY)
    assert snap.tss_7d == 0.0
    assert snap.tss_6w_avg == 0.0
    assert snap.load_trend_pct == 0.0
    assert snap.sessions_done_7d == 0
    assert snap.monotony_index is None


def test_snapshot_returns_weeklysnapshottype():
    snap = compute_weekly_snapshot([], TODAY)
    assert isinstance(snap, WeeklySnapshot)


# ── Fenêtre 7 jours ────────────────────────────────────────────────────────────

def test_tss_7d_includes_boundary_day():
    """Le jour today-6 est inclus dans la fenêtre."""
    logs = [
        _log(TODAY, 80),
        _log(TODAY - timedelta(days=6), 50),   # borne incluse
    ]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 130.0
    assert snap.sessions_done_7d == 2


def test_tss_7d_excludes_day_before_boundary():
    """Le jour today-7 est hors fenêtre."""
    logs = [
        _log(TODAY, 80),
        _log(TODAY - timedelta(days=7), 100),  # hors fenêtre
    ]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 80.0
    assert snap.sessions_done_7d == 1


def test_skipped_sessions_not_counted_in_7d():
    logs = [
        _log(TODAY, 80, status="done"),
        _log(TODAY - timedelta(days=1), 60, status="skipped"),
    ]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 80.0
    assert snap.sessions_done_7d == 1


def test_multiple_sessions_same_day_cumulated():
    """Deux séances le même jour sont cumulées pour le TSS journalier (monotonie)."""
    logs = [
        _log(TODAY, 60),
        _log(TODAY, 40),  # même jour
    ]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 100.0
    assert snap.sessions_done_7d == 2


# ── Fenêtre 6 semaines ─────────────────────────────────────────────────────────

def test_6w_average_excludes_current_7d_window():
    """Les sessions de la semaine en cours n'entrent pas dans la moyenne 6 semaines."""
    logs = [
        _log(TODAY, 200),                        # semaine courante
        _log(TODAY - timedelta(days=10), 100),   # semaine -2
    ]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 200.0
    # La semaine courante ne doit pas influencer la moyenne 6 semaines
    assert snap.tss_6w_avg < 200.0


def test_load_trend_positive_when_current_week_higher_than_average():
    logs = []
    # 6 semaines passées à 100 TSS/semaine (1 session par semaine)
    for w in range(1, 7):
        logs.append(_log(TODAY - timedelta(days=7 * w + 3), 100.0))
    # Semaine courante : 150 TSS
    logs.append(_log(TODAY, 150.0))

    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.tss_7d == 150.0
    assert snap.load_trend_pct > 0


def test_load_trend_negative_when_current_week_lower():
    logs = []
    for w in range(1, 7):
        logs.append(_log(TODAY - timedelta(days=7 * w + 3), 200.0))
    logs.append(_log(TODAY, 50.0))

    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.load_trend_pct < 0


def test_load_trend_zero_when_no_past_history():
    logs = [_log(TODAY, 100.0)]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.load_trend_pct == 0.0


# ── Monotonie Foster ───────────────────────────────────────────────────────────

def test_monotony_none_with_single_training_day():
    """Pas de std calculable avec un seul jour → None."""
    logs = [_log(TODAY, 100.0)]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.monotony_index is None


def test_monotony_none_when_std_is_zero():
    """Charge parfaitement uniforme → std = 0 → None (garde division par zéro)."""
    logs = [_log(TODAY - timedelta(days=i), 100.0) for i in range(6)]
    snap = compute_weekly_snapshot(logs, TODAY)
    # std=0 → guard retourne None
    assert snap.monotony_index is None


def test_monotony_computed_with_varied_daily_load():
    """Charge variée → monotonie calculable (mean/std > 0)."""
    # [90, 110, 90, 110, 90, 110] → std ≈ 10, monotony = 100/10 = 10 (élevée)
    loads = [90, 110, 90, 110, 90, 110]
    logs = [_log(TODAY - timedelta(days=i), float(loads[i])) for i in range(6)]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.monotony_index is not None
    assert snap.monotony_index > 0


def test_monotony_high_for_nearly_uniform_load():
    """Charge très uniforme (faible std) → indice Foster très élevé → danger."""
    # [99, 101, 99, 101] → mean=100, std≈1.15, Foster=100/1.15≈87
    loads = [99.0, 101.0, 99.0, 101.0]
    logs = [_log(TODAY - timedelta(days=i), loads[i]) for i in range(4)]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.monotony_index is not None
    assert snap.monotony_index > 2.0  # nettement au-dessus du seuil danger


def test_monotony_low_for_highly_varied_load():
    """Charge très variée (grand std) → indice Foster faible → bonne variété."""
    # [10, 190, 10, 190] → mean=100, std≈104, Foster≈0.96 < 2.0
    loads = [10.0, 190.0, 10.0, 190.0]
    logs = [_log(TODAY - timedelta(days=i), loads[i]) for i in range(4)]
    snap = compute_weekly_snapshot(logs, TODAY)
    assert snap.monotony_index is not None
    assert snap.monotony_index < 2.0
