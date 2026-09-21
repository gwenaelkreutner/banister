"""Tests pour app/engine/dfa.py — DFA α1 par franchissement de bande (2026-09-21, porté
depuis Section11, sur des séries synthétiques — aucune activité AlphaHRV réelle
n'existe encore sur le compte de test, voir le docstring du module).
"""
from app.engine.dfa import (
    DFA_LT1,
    DFA_LT2,
    DFA_MIN_DURATION_SECS,
    compute_dfa_block,
)


def test_no_dfa_a1_stream_returns_none():
    assert compute_dfa_block({}) is None
    assert compute_dfa_block({"heartrate": [140] * 100}) is None


def test_empty_dfa_a1_stream_returns_none():
    assert compute_dfa_block({"dfa_a1": []}) is None


def test_sentinel_zeros_are_filtered_out():
    # 100 secondes valides à 0.9, mélangées à 50 zéros-sentinelles AlphaHRV.
    dfa = [0.0] * 25 + [0.9] * 100 + [0.0] * 25
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.quality.valid_secs == 100
    assert result.quality.total_secs == 150


def test_high_artifact_seconds_are_dropped():
    n = 1300
    dfa = [0.9] * n
    artifacts = [10.0] * 100 + [0.0] * (n - 100)  # 100s au-dessus du seuil 5%
    result = compute_dfa_block({"dfa_a1": dfa, "artifacts": artifacts})
    assert result is not None
    assert result.quality.valid_secs == n - 100
    assert result.quality.artifact_rate_avg is not None


def test_below_min_duration_is_insufficient():
    dfa = [0.9] * 500  # < DFA_MIN_DURATION_SECS (1200)
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.quality.sufficient is False
    assert result.avg is None  # bloc minimal, pas de rollup halluciné
    assert result.quality.valid_secs == 500


def test_low_valid_pct_is_insufficient_even_with_enough_raw_seconds():
    # 1300s brutes mais seulement 600 valides (46% < seuil 70%) -> insuffisant, même
    # si valid_secs (600) est... non, 600 < DFA_MIN_DURATION_SECS aussi ici — testons
    # le cas où valid_secs >= 1200 mais valid_pct < 70% séparément avec plus de secondes.
    dfa = [0.9] * 1300 + [0.0] * 1700  # 1300 valides (>=1200) mais 43% du total
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.quality.valid_secs == 1300
    assert result.quality.valid_pct < 70.0
    assert result.quality.sufficient is False


def test_sufficient_steady_state_computes_full_rollup():
    n = DFA_MIN_DURATION_SECS + 100
    dfa = [0.9] * n  # dans la bande lt1_transition (0.75-1.0)
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.quality.sufficient is True
    assert result.avg == 0.9
    assert result.p25 == result.p50 == result.p75 == 0.9
    assert result.tiz_lt1_transition is not None
    assert result.tiz_lt1_transition.pct == 100.0
    assert result.tiz_below_lt1 is None
    assert result.tiz_transition_lt2 is None
    assert result.tiz_above_lt2 is None


def test_drift_delta_and_interpretability():
    third = 500
    # Dérive : commence à 0.9 (dans lt1_transition), descend à 0.7 (transition_lt2) —
    # peu de temps au-dessus de LT2, donc la dérive doit rester interprétable.
    dfa = [0.9] * third + [0.8] * third + [0.7] * third
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.drift is not None
    assert result.drift.first_third_avg == 0.9
    assert result.drift.last_third_avg == 0.7
    assert result.drift.delta == -0.2
    assert result.drift.interpretable is True


def test_drift_not_interpretable_when_too_much_time_above_lt2():
    third = 500
    # Beaucoup de temps sous DFA_LT2 (0.5) = beaucoup de temps supra-seuil -> dérive
    # structurelle (intervalles), pas interprétable comme une vraie dérive aérobie.
    dfa = [0.9] * third + [0.9] * third + [0.3] * third
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.drift is not None
    assert result.drift.interpretable is False


def test_crossing_stats_require_minimum_dwell():
    n = DFA_MIN_DURATION_SECS + 10
    # Seulement 30s dans la bande de franchissement LT1 (< DFA_MIN_CROSSING_DWELL_SECS=60)
    dfa = [0.9] * (n - 30) + [1.0] * 30
    result = compute_dfa_block({"dfa_a1": dfa})
    assert result is not None
    assert result.lt1_crossing is not None
    assert result.lt1_crossing.secs_in_band == 30
    assert result.lt1_crossing.avg_hr is None  # sous le dwell minimum, pas de moyenne


def test_crossing_stats_computed_with_hr_and_watts_when_dwell_met():
    n = DFA_MIN_DURATION_SECS + 100
    dfa = [0.9] * (n - 100) + [1.0] * 100  # 100s >= DFA_MIN_CROSSING_DWELL_SECS
    hr = [150] * (n - 100) + [160] * 100
    watts = [200] * (n - 100) + [210] * 100
    result = compute_dfa_block({"dfa_a1": dfa, "heartrate": hr, "watts": watts})
    assert result is not None
    assert result.lt1_crossing.secs_in_band == 100
    assert result.lt1_crossing.avg_hr == 160
    assert result.lt1_crossing.avg_watts == 210


def test_mismatched_stream_lengths_are_padded_defensively():
    n = DFA_MIN_DURATION_SECS + 50
    dfa = [0.9] * n
    hr = [150] * 10  # bien plus court que dfa_a1
    result = compute_dfa_block({"dfa_a1": dfa, "heartrate": hr})
    assert result is not None  # ne plante pas malgré la longueur différente


def test_lt1_and_lt2_constants_match_cited_values():
    assert DFA_LT1 == 1.0
    assert DFA_LT2 == 0.5
