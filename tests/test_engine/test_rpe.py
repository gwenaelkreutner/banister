"""Tests pour app/engine/rpe.py — échelle RPE standard 1-10 (2026-09-21, remplace
l'ancien rpe_emoji catégoriel)."""
from app.engine.rpe import (
    RPE_EASY_MAX,
    RPE_EASY_VALUE,
    RPE_HARD_MIN,
    RPE_HARD_VALUE,
    RPE_NORMAL_VALUE,
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


def test_representative_values_land_in_the_expected_band():
    """Les 3 boutons Telegram écrivent ces valeurs — elles doivent retomber dans la
    bande que leur nom suggère, sinon le bouton "Facile" pourrait afficher 😫 ailleurs."""
    assert rpe_band(RPE_EASY_VALUE) == "easy"
    assert rpe_band(RPE_NORMAL_VALUE) == "normal"
    assert rpe_band(RPE_HARD_VALUE) == "hard"


def test_emoji_none_is_a_dash():
    assert rpe_emoji(None) == "—"


def test_emoji_matches_band():
    assert rpe_emoji(RPE_EASY_VALUE) == "🙂"
    assert rpe_emoji(RPE_NORMAL_VALUE) == "😐"
    assert rpe_emoji(RPE_HARD_VALUE) == "😫"


def test_label_shows_integer_without_decimal():
    assert rpe_label(8) == "8/10 (dur)"


def test_label_keeps_one_decimal_for_non_integer_values():
    assert rpe_label(6.5) == "6.5/10 (modéré)"
