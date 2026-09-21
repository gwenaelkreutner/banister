"""
Tests pour les helpers déterministes de app/llm/activity_analysis.py.

Seules les fonctions pures (sans appel LLM) sont testées ici :
  _compute_match_score, _detect_rpe_mismatch, _tsb_tone.
"""

from app.llm.activity_analysis import (
    _compute_match_score,
    _detect_rpe_mismatch,
    _tsb_tone,
)


# ── _compute_match_score ───────────────────────────────────────────────────────

def test_match_score_within_15pct_returns_check():
    flag, text = _compute_match_score(100, 110)
    assert flag == "✅"
    assert "110" in text
    assert "+10%" in text


def test_match_score_exactly_15pct_still_check():
    flag, _ = _compute_match_score(100, 115)
    assert flag == "✅"


def test_match_score_between_15_and_30pct_returns_warning():
    flag, _ = _compute_match_score(100, 125)
    assert flag == "⚠️"


def test_match_score_over_30pct_returns_chart():
    flag, _ = _compute_match_score(100, 145)
    assert flag == "📊"


def test_match_score_deficit_over_30pct_returns_chart():
    flag, text = _compute_match_score(100, 60)
    assert flag == "📊"
    assert "-40%" in text


def test_match_score_with_none_planned_returns_empty():
    flag, text = _compute_match_score(None, 100)
    assert flag == ""
    assert text == ""


def test_match_score_with_none_actual_returns_empty():
    flag, text = _compute_match_score(100, None)
    assert flag == ""
    assert text == ""


def test_match_score_with_zero_planned_returns_empty():
    flag, text = _compute_match_score(0, 100)
    assert flag == ""
    assert text == ""


# ── _detect_rpe_mismatch ───────────────────────────────────────────────────────

def test_rpe_mismatch_no_rpe_asks_to_fill_in():
    msg = _detect_rpe_mismatch(None, 100, 100)
    assert msg is not None
    assert "renseigner" in msg.lower()


def test_rpe_mismatch_hard_with_low_tss_suggests_ftp():
    """RPE dur (>= RPE_HARD_MIN) mais TSS < 85% du prévu → suspect, suggère vérif FTP."""
    msg = _detect_rpe_mismatch(8, 70, 100)  # ratio 0.70 < 0.85
    assert msg is not None
    assert "FTP" in msg


def test_rpe_mismatch_hard_with_high_tss_returns_none():
    """RPE dur et TSS au-dessus du prévu → cohérent."""
    msg = _detect_rpe_mismatch(8, 110, 100)
    assert msg is None


def test_rpe_mismatch_easy_with_high_tss_signals_anomaly():
    """RPE facile (<= RPE_EASY_MAX) mais TSS > 115% → forme ou FTP sous-estimé."""
    msg = _detect_rpe_mismatch(3, 125, 100)  # ratio 1.25 > 1.15
    assert msg is not None
    assert "FTP" in msg


def test_rpe_mismatch_easy_with_normal_tss_returns_none():
    msg = _detect_rpe_mismatch(3, 95, 100)
    assert msg is None


def test_rpe_mismatch_normal_returns_none():
    msg = _detect_rpe_mismatch(5, 100, 100)
    assert msg is None


def test_rpe_mismatch_without_tss_values_returns_none_for_valid_rpe():
    """Sans données TSS, pas d'alerte possible si RPE est renseigné."""
    msg = _detect_rpe_mismatch(8, None, None)
    assert msg is None


# ── _tsb_tone ──────────────────────────────────────────────────────────────────

def test_tsb_tone_protective_below_minus_30():
    tone = _tsb_tone(-35)
    assert "protecteur" in tone


def test_tsb_tone_protective_at_exact_minus_30():
    """La borne -30 déclenche le ton protecteur (strict <)."""
    tone = _tsb_tone(-30.1)
    assert "protecteur" in tone


def test_tsb_tone_balanced_at_minus_30():
    """TSB exactement -30 → équilibré (pas encore surmenage)."""
    tone = _tsb_tone(-30)
    assert "équilibré" in tone


def test_tsb_tone_motivating_at_plus_5():
    tone = _tsb_tone(5)
    assert "motivant" in tone


def test_tsb_tone_motivating_above_5():
    tone = _tsb_tone(12)
    assert "motivant" in tone


def test_tsb_tone_balanced_just_below_5():
    tone = _tsb_tone(4.9)
    assert "équilibré" in tone


def test_tsb_tone_balanced_in_middle():
    tone = _tsb_tone(-10)
    assert "équilibré" in tone


def test_tsb_tone_none_returns_balanced():
    tone = _tsb_tone(None)
    assert "équilibré" in tone
