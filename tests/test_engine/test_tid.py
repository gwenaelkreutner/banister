"""Tests pour app/engine/tid.py — TID / indice de polarisation (2026-09-21).

Vérifie : agrégation multi-séances, fenêtre glissante, garde-fous du domaine du log,
exclusion de "SS", classification.
"""
from datetime import date, timedelta
from types import SimpleNamespace

from app.engine.tid import compute_tid

TODAY = date(2026, 9, 21)


def _log(logged_date: date, time_in_zones_s: dict) -> SimpleNamespace:
    return SimpleNamespace(logged_date=logged_date, time_in_zones_s=time_in_zones_s)


def test_no_logs_returns_none():
    assert compute_tid([], today=TODAY, window_days=7) is None


def test_logs_without_zone_data_are_ignored():
    logs = [_log(TODAY, {}), _log(TODAY, None)]
    assert compute_tid(logs, today=TODAY, window_days=7) is None


def test_logs_outside_the_window_are_excluded():
    logs = [_log(TODAY - timedelta(days=10), {"Z1": 3600})]
    assert compute_tid(logs, today=TODAY, window_days=7) is None


def test_aggregates_across_multiple_sessions():
    logs = [
        _log(TODAY, {"Z1": 3600}),
        _log(TODAY - timedelta(days=1), {"Z1": 1800, "Z5": 600}),
    ]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result is not None
    assert result.sessions_counted == 2
    assert result.total_seconds == 3600 + 1800 + 600


def test_ss_bucket_is_excluded_like_dominant_zone():
    """SS (sweet spot) chevauche Z3/Z4 — l'inclure doublerait le temps déjà compté
    ailleurs (même précédent que mapper.py::_dominant_zone)."""
    logs = [_log(TODAY, {"Z1": 1000, "SS": 5000})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.total_seconds == 1000  # SS n'entre dans aucun bucket


def test_zone_bucketing_z1_z2_low_z3_z4_moderate_z5_z6_z7_high():
    logs = [_log(TODAY, {
        "Z1": 100, "Z2": 100, "Z3": 100, "Z4": 100, "Z5": 100, "Z6": 100, "Z7": 100,
    })]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.zone1_pct == round(200 / 700 * 100, 1)
    assert result.zone2_pct == round(200 / 700 * 100, 1)
    assert result.zone3_pct == round(300 / 700 * 100, 1)


def test_polarization_index_is_none_when_a_zone_is_zero():
    """Domaine du log : Z2 (ou Z1/Z3) à 0% ferait planter log10 — doit rester None,
    jamais un chiffre halluciné."""
    logs = [_log(TODAY, {"Z1": 1000, "Z5": 200})]  # zone2 = 0
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.zone2_pct == 0.0
    assert result.polarization_index is None


def test_classic_polarized_distribution_classifies_as_polarized():
    """80/5/15, le profil polarisé canonique (Seiler) — PI attendu ≈ 2.38."""
    logs = [_log(TODAY, {"Z1": 8000, "Z3": 500, "Z5": 1500})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.classification == "polarized"
    assert result.polarization_index == 2.38


def test_almost_entirely_zone1_classifies_as_base():
    """95/3/2 — PI≈1.80 (sous le seuil polarisé) mais zone1 ≥ 90% : base l'emporte."""
    logs = [_log(TODAY, {"Z1": 9500, "Z3": 300, "Z5": 200})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.zone1_pct >= 90.0
    assert result.classification == "base"


def test_zone2_dominant_classifies_as_threshold():
    """20/60/20 — PI≈0.82, largement sous le seuil polarisé : la zone 2 domine sans
    ambiguïté (60%)."""
    logs = [_log(TODAY, {"Z1": 2000, "Z3": 6000, "Z5": 2000})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.zone2_pct >= 30.0
    assert result.classification == "threshold"


def test_high_zone3_share_classifies_as_high_intensity():
    """20/20/60 — PI≈1.78, sous le seuil : la zone 3 domine largement (60%)."""
    logs = [_log(TODAY, {"Z1": 2000, "Z3": 2000, "Z5": 6000})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.classification == "high_intensity"


def test_descending_shape_without_polarization_classifies_as_pyramidal():
    """65/25/10 — PI≈1.41, sous le seuil polarisé mais zone1 > zone2 > zone3 : forme
    pyramidale classique (moins extrême qu'un profil polarisé)."""
    logs = [_log(TODAY, {"Z1": 6500, "Z3": 2500, "Z5": 1000})]
    result = compute_tid(logs, today=TODAY, window_days=7)
    assert result.zone1_pct > result.zone2_pct > result.zone3_pct
    assert result.classification == "pyramidal"
