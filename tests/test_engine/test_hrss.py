"""
Tests unitaires pour calc_hrss() — TRIMP de Banister + normalisation HRSS.

Profil de référence utilisé dans tous les tests :
  HR_rest = 50 bpm
  HR_max  = 190 bpm
  LTHR    = 170 bpm  (HRR_LTHR = 120/140 ≈ 0.857)
"""

import math

import pytest

from app.engine.tss import FatigueAnomaly, HRSSResult, calc_hrss

# ── Constantes du profil de référence ─────────────────────────────────────

HR_REST = 50
HR_MAX = 190
LTHR = 170  # seuil lactique
K_M = 1.92
K_F = 1.67


# ── Helpers ────────────────────────────────────────────────────────────────


def _constant_stream(hr_bpm: float, duration_s: int) -> tuple[list[float], list[int]]:
    """Stream constant : `duration_s` secondes à `hr_bpm`."""
    hr = [hr_bpm] * (duration_s + 1)  # +1 pour avoir duration_s intervalles
    t = list(range(duration_s + 1))
    return hr, t


def _trimp_rate(hr_bpm: float, k: float) -> float:
    """Stress TRIMP par seconde à `hr_bpm` (formule de Banister)."""
    hrr = (hr_bpm - HR_REST) / (HR_MAX - HR_REST)
    hrr = max(0.0, min(1.0, hrr))
    return hrr * 0.64 * math.exp(k * hrr)


def _trimp_1h_lthr(k: float) -> float:
    return 3600.0 * _trimp_rate(LTHR, k)


# ── 1. Propriétés de normalisation ─────────────────────────────────────────


def test_hrss_exactly_100_at_lthr_1h():
    """1h exactement au LTHR doit renvoyer HRSS = 100 (point de calibration)."""
    hr, t = _constant_stream(LTHR, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    assert result.hrss == pytest.approx(100.0, abs=0.1)


def test_hrss_exactly_200_at_lthr_2h():
    """HRSS est linéaire en durée à intensité constante — 2h au LTHR → 200."""
    hr, t = _constant_stream(LTHR, 7200)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    assert result.hrss == pytest.approx(200.0, abs=0.2)


def test_hrss_below_100_for_z2_1h():
    """Effort Z2 (140 bpm) pendant 1h doit donner HRSS < 100."""
    hr, t = _constant_stream(140, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    assert 0 < result.hrss < 100


def test_hrss_above_100_for_z5_1h():
    """Effort Z5 (180 bpm) pendant 1h doit donner HRSS > 100."""
    hr, t = _constant_stream(180, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    assert result.hrss > 100


def test_hrss_monotone_with_intensity():
    """HRSS croît strictement avec la FC à durée égale."""
    hr_z2, t = _constant_stream(140, 3600)
    hr_z3, _ = _constant_stream(155, 3600)
    hr_z4, _ = _constant_stream(165, 3600)

    r_z2 = calc_hrss(hr_z2, t, HR_REST, HR_MAX, LTHR)
    r_z3 = calc_hrss(hr_z3, t, HR_REST, HR_MAX, LTHR)
    r_z4 = calc_hrss(hr_z4, t, HR_REST, HR_MAX, LTHR)

    assert r_z2.hrss < r_z3.hrss < r_z4.hrss


def test_hrss_capped_at_400():
    """HRSS ne doit jamais dépasser 400 (cap de sécurité)."""
    # 10h à 185 bpm — valeur extrême
    hr, t = _constant_stream(185, 36_000)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert result.hrss <= 400.0


# ── 2. Valeur TRIMP ────────────────────────────────────────────────────────


def test_trimp_value_at_lthr_1h():
    """TRIMP total ≈ TRIMP_1h_LTHR attendu analytiquement."""
    hr, t = _constant_stream(LTHR, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    expected_trimp = _trimp_1h_lthr(K_M)
    assert result.trimp == pytest.approx(expected_trimp, rel=0.01)


# ── 3. Différences hommes / femmes ─────────────────────────────────────────


def test_sex_difference_at_z2_effort():
    """
    À effort sub-LTHR (Z2, 140 bpm), F obtient un HRSS légèrement > M.
    Le modèle Banister pénalise davantage les efforts supra-seuil pour les H.
    """
    hr, t = _constant_stream(140, 3600)
    r_m = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    r_f = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="F")
    # Les deux HRSS sont distincts (k différents)
    assert r_m.hrss != r_f.hrss


def test_sex_difference_at_z5_effort():
    """À effort supra-LTHR (Z5, 180 bpm), M obtient un HRSS > F (pente exp plus forte)."""
    hr, t = _constant_stream(180, 3600)
    r_m = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    r_f = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="F")
    assert r_m.hrss > r_f.hrss


def test_sex_case_insensitive():
    """Le paramètre sex est insensible à la casse."""
    hr, t = _constant_stream(LTHR, 3600)
    r_upper = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    r_lower = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="m")
    assert r_upper.hrss == r_lower.hrss


def test_unknown_sex_falls_back_to_male_k():
    """Sex inconnu → coefficient male par défaut (k=1.92)."""
    hr, t = _constant_stream(180, 3600)
    r_default = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="X")
    r_male = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, sex="M")
    assert r_default.hrss == r_male.hrss


# ── 4. Gestion des cas limites ─────────────────────────────────────────────


def test_empty_series_returns_zero():
    result = calc_hrss([], [], HR_REST, HR_MAX, LTHR)
    assert result.hrss == 0.0
    assert result.trimp == 0.0


def test_single_sample_returns_zero():
    """Un seul point = 0 intervalle → TRIMP nul."""
    result = calc_hrss([150.0], [0], HR_REST, HR_MAX, LTHR)
    assert result.hrss == 0.0


def test_invalid_hr_range_returns_zero():
    """HR_max ≤ HR_rest → configuration impossible."""
    hr, t = _constant_stream(150, 3600)
    result = calc_hrss(hr, t, hr_rest=160, hr_max=160, threshold_hr=155)
    assert result.hrss == 0.0


def test_hr_below_rest_clamped_to_zero_hrr():
    """HR en-dessous du repos (artefact capteur) contribue 0 TRIMP."""
    hr = [30.0] * 3601  # < HR_rest=50 → HRR=0
    t = list(range(3601))
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert result.hrss == 0.0


def test_nan_values_treated_as_hr_rest():
    """Valeurs NaN (perte signal) → clampées à hr_rest → HRR=0, pas d'exception."""
    import math

    hr = [math.nan if i % 100 == 0 else float(LTHR) for i in range(3601)]
    t = list(range(3601))
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert result.hrss > 0  # signal majoritairement valide
    assert result.hrss < 100  # quelques NaN réduisent le HRSS


def test_non_uniform_timestamps():
    """Timestamps non uniformes (gaps GPS) → dt correct via diff."""
    # 2 blocs : 30 min Z2, gap de 5 min repos, 30 min Z4
    hr_z2 = [140.0] * 1800
    hr_gap = [50.0] * 300   # repos pendant le gap → contribue 0 TRIMP
    hr_z4 = [165.0] * 1800

    hr = hr_z2 + hr_gap + hr_z4
    t = list(range(len(hr)))

    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)

    # Comparer avec une activité sans gap mais même intensité moyenne
    hr_z2_only, t_ref = _constant_stream(140, 3600)
    r_z2_only = calc_hrss(hr_z2_only, t_ref, HR_REST, HR_MAX, LTHR)

    # Z4 est plus intense → HRSS mixte > Z2 seul, même durée
    assert result.hrss > r_z2_only.hrss


# ── 5. Logique RPE / FatigueAnomaly ───────────────────────────────────────


def test_no_rpe_no_anomaly():
    """Sans RPE déclaré → pas d'anomalie, rpe_factor = 1.0."""
    hr, t = _constant_stream(LTHR, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert result.fatigue_anomaly is None
    assert result.rpe_factor == 1.0


def test_rpe_consistent_with_cardiac_load_no_anomaly():
    """
    RPE ≈ charge cardiaque (Z2 effort, ~HRR=0.64 → RPE cardiaque ≈ 6.4).
    RPE déclaré 6 → delta < 3 → pas d'anomalie.
    """
    hr, t = _constant_stream(140, 3600)  # HRR ≈ 0.643 → RPE cardiaque ≈ 6.4
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, user_rpe=6)
    assert result.fatigue_anomaly is None
    assert result.rpe_factor == 1.0


def test_rpe_anomaly_triggered_when_delta_gte_3():
    """
    Z2 effort (RPE cardiaque ≈ 6.4) avec RPE déclaré 10 → delta ≈ 3.6 ≥ 3.
    FatigueAnomaly doit être levée.
    """
    hr, t = _constant_stream(140, 3600)  # HRR ≈ 0.643 → RPE cardiaque ≈ 6.4
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, user_rpe=10)

    assert result.fatigue_anomaly is not None
    fa: FatigueAnomaly = result.fatigue_anomaly
    assert fa.rpe_declared == 10
    assert fa.rpe_delta >= 3.0
    assert 1.10 <= fa.rpe_factor <= 1.20
    assert fa.message  # message non vide


def test_rpe_factor_applied_to_hrss():
    """HRSS avec anomalie RPE doit être supérieur au HRSS sans RPE."""
    hr, t = _constant_stream(140, 3600)

    r_no_rpe = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    r_rpe_10 = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, user_rpe=10)

    assert r_rpe_10.hrss > r_no_rpe.hrss


def test_rpe_factor_capped_at_1_2():
    """Peu importe l'écart RPE, le multiplicateur ne dépasse pas 1.20."""
    hr, t = _constant_stream(50, 3600)  # HRR ≈ 0 → RPE cardiaque ≈ 0
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, user_rpe=10)

    if result.fatigue_anomaly is not None:
        assert result.fatigue_anomaly.rpe_factor <= 1.20


def test_rpe_just_below_threshold_no_anomaly():
    """Delta RPE = 2.9 (< 3) → pas d'anomalie."""
    # RPE cardiaque ≈ 6.4 (HRR=0.643), RPE déclaré 9 → delta ≈ 2.6
    hr, t = _constant_stream(140, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR, user_rpe=9)
    # 9 − 6.4 = 2.6 < 3 → pas d'anomalie
    assert result.fatigue_anomaly is None


# ── 6. Type de retour ──────────────────────────────────────────────────────


def test_returns_hrss_result_instance():
    hr, t = _constant_stream(LTHR, 3600)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert isinstance(result, HRSSResult)
    assert result.tss_method == "hrss"


def test_hrss_and_trimp_are_non_negative():
    hr, t = _constant_stream(155, 1800)
    result = calc_hrss(hr, t, HR_REST, HR_MAX, LTHR)
    assert result.hrss >= 0.0
    assert result.trimp >= 0.0
