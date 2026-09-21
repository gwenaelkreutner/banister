"""Tests pour app/engine/power_curve.py — power-curve delta + sustainability_profile
(2026-09-21, porté depuis Section11, formule vérifiée contre l'API réelle intervals.icu).
"""
from app.engine.power_curve import (
    COGGAN_DURATION_FACTORS,
    compute_power_curve_delta,
    compute_sustainability_profile,
)

CUR_ID = "r.2026-08-25.2026-09-21"
PREV_ID = "r.2026-07-28.2026-08-24"


def _curve(curve_id: str, secs: list[int], watts: list[int]) -> dict:
    return {"id": curve_id, "secs": secs, "watts": watts}


def _response(*curves: dict) -> dict:
    return {"list": list(curves), "activities": []}


# ── compute_power_curve_delta ────────────────────────────────────────────────


def test_missing_current_window_returns_note_no_crash():
    response = _response(_curve(PREV_ID, [5, 60], [1000, 400]))
    result = compute_power_curve_delta(
        response, current_window_id=CUR_ID, previous_window_id=PREV_ID
    )
    assert result.rotation_index is None
    assert "actuelle" in result.note


def test_too_few_valid_anchors_returns_note():
    # Un seul ancrage valide (5s) sur les 5 attendus dans chaque fenêtre.
    response = _response(
        _curve(CUR_ID, [5], [1000]),
        _curve(PREV_ID, [5], [980]),
    )
    result = compute_power_curve_delta(
        response, current_window_id=CUR_ID, previous_window_id=PREV_ID
    )
    assert result.rotation_index is None
    assert "minimum" in result.note


def test_full_anchors_computes_pct_change_and_rotation_index():
    secs = [5, 60, 300, 1200, 3600]
    response = _response(
        _curve(CUR_ID, secs, [1100, 500, 300, 250, 200]),
        _curve(PREV_ID, secs, [1000, 400, 300, 250, 200]),
    )
    result = compute_power_curve_delta(
        response, current_window_id=CUR_ID, previous_window_id=PREV_ID
    )
    assert result.note is None
    assert result.anchors["5s"].pct_change == 10.0
    assert result.anchors["60s"].pct_change == 25.0
    assert result.anchors["1200s"].pct_change == 0.0
    assert result.anchors["3600s"].pct_change == 0.0
    # rotation = mean(10, 25) - mean(0, 0) = 17.5
    assert result.rotation_index == 17.5


def test_negative_rotation_index_means_endurance_biased():
    secs = [5, 60, 300, 1200, 3600]
    response = _response(
        _curve(CUR_ID, secs, [1000, 400, 300, 275, 220]),
        _curve(PREV_ID, secs, [1000, 400, 300, 250, 200]),
    )
    result = compute_power_curve_delta(
        response, current_window_id=CUR_ID, previous_window_id=PREV_ID
    )
    assert result.rotation_index is not None and result.rotation_index < 0


def test_zero_watts_anchor_value_is_excluded_like_none():
    secs = [5, 60, 300, 1200, 3600]
    response = _response(
        _curve(CUR_ID, secs, [1100, 500, 300, 0, 200]),
        _curve(PREV_ID, secs, [1000, 400, 300, 250, 200]),
    )
    result = compute_power_curve_delta(
        response, current_window_id=CUR_ID, previous_window_id=PREV_ID
    )
    assert result.anchors["1200s"].current_watts is None
    assert result.anchors["1200s"].pct_change is None


# ── compute_sustainability_profile ───────────────────────────────────────────


WINDOW_ID = "r.2026-08-11.2026-09-21"  # 42 jours


def test_no_curve_for_window_returns_empty_but_no_crash():
    result = compute_sustainability_profile(
        [_response(_curve("other-id", [300], [280]))],
        window_id=WINDOW_ID, ftp=280.0, w_prime=20000.0,
    )
    assert result.coverage_ratio == 0.0
    assert "minimum" in result.note


def test_below_min_observed_anchors_returns_note():
    response = _response(_curve(WINDOW_ID, [300], [300]))  # 1 seul ancrage
    result = compute_sustainability_profile(
        [response], window_id=WINDOW_ID, ftp=280.0, w_prime=20000.0
    )
    assert result.note is not None and "minimum" in result.note


def test_coggan_and_cp_model_computed_at_known_anchors():
    secs = [300, 600, 1200, 1800, 3600, 5400, 7200]
    watts = [320, 290, 270, 260, 250, 230, 210]
    response = _response(_curve(WINDOW_ID, secs, watts))
    result = compute_sustainability_profile(
        [response], window_id=WINDOW_ID, ftp=250.0, w_prime=20000.0, weight_kg=70.0
    )
    assert result.note is None
    a300 = result.anchors["300s"]
    assert a300.actual_watts == 320
    assert a300.actual_wpkg == round(320 / 70.0, 2)
    assert a300.coggan_watts == round(250.0 * COGGAN_DURATION_FACTORS[300])
    assert a300.cp_model_watts == round(250.0 + 20000.0 / 300)
    assert a300.model_divergence_pct == round(
        (320 - a300.cp_model_watts) / a300.cp_model_watts * 100, 1
    )


def test_merges_best_watts_across_multiple_activity_types():
    # Ride donne 300W à 300s, VirtualRide donne 310W — le meilleur doit gagner (indoor
    # peut légitimement dépasser outdoor, même logique que Section11).
    ride = _response(_curve(WINDOW_ID, [300, 600, 1200], [300, 280, 260]))
    virtual_ride = _response(_curve(WINDOW_ID, [300, 600, 1200], [310, 270, 255]))
    result = compute_sustainability_profile(
        [ride, virtual_ride], window_id=WINDOW_ID, ftp=250.0, w_prime=20000.0
    )
    assert result.anchors["300s"].actual_watts == 310  # VirtualRide gagne
    assert result.anchors["600s"].actual_watts == 280  # Ride gagne


def test_missing_ftp_or_w_prime_leaves_models_none_without_crashing():
    secs = [300, 600, 1200]
    response = _response(_curve(WINDOW_ID, secs, [300, 280, 260]))
    result = compute_sustainability_profile([response], window_id=WINDOW_ID, ftp=None, w_prime=None)
    assert result.note is None  # coverage suffisante (3 ancrages observés)
    assert result.anchors["300s"].coggan_watts is None
    assert result.anchors["300s"].cp_model_watts is None
    assert result.anchors["300s"].model_divergence_pct is None
