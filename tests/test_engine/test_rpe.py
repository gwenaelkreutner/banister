"""Tests pour app/engine/rpe.py — échelle RPE standard 1-10 (2026-09-21, remplace
l'ancien rpe_emoji catégoriel)."""
from app.engine.rpe import (
    RPE_EASY_MAX,
    RPE_HARD_MIN,
    RPE_SCALE_LABELS_FR,
    rpe_band,
    rpe_emoji,
    rpe_label,
)


def test_hard_band_at_and_above_threshold():
    assert rpe_band(RPE_HARD_MIN) == "hard"
    assert rpe_band(10) == "hard"


def test_easy_band_at_and_below_threshold():
    assert rpe_band(RPE_EASY_MAX) == "easy"
    assert rpe_band(1) == "easy"


def test_normal_band_between_thresholds():
    assert rpe_band(5) == "normal"


def test_scale_has_the_ten_ordered_choices():
    assert len(RPE_SCALE_LABELS_FR) == 10
    assert RPE_SCALE_LABELS_FR[0] == "Aucun effort"
    assert RPE_SCALE_LABELS_FR[4] == "Légèrement difficile"
    assert RPE_SCALE_LABELS_FR[-1] == "Effort maximal"


def test_emoji_none_is_a_dash():
    assert rpe_emoji(None) == "—"


def test_emoji_matches_band():
    assert rpe_emoji(3) == "🙂"
    assert rpe_emoji(5) == "😐"
    assert rpe_emoji(8) == "😫"


def test_label_shows_integer_without_decimal():
    assert rpe_label(8) == "8/10 (dur)"


def test_label_keeps_one_decimal_for_non_integer_values():
    assert rpe_label(6.5) == "6.5/10 (modéré)"
