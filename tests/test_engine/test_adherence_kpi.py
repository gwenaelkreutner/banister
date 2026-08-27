"""Tests du moteur KPI d'adhérence."""

import pytest
from app.engine.adherence_kpi import (
    KPIContribution,
    compute_session_kpi,
    compute_cumulative_kpi,
    kpi_delta_message,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kpi(
    tss_planned=100.0,
    week_tss=400.0,
    weeks_total=10,
    tss_actual=100.0,
    workout_type="endurance",
    session_type_real="endurance",
    tsb_after=0.0,
    level="intermediate",
) -> KPIContribution:
    return compute_session_kpi(
        tss_planned=tss_planned,
        week_tss_planned=week_tss,
        weeks_total=weeks_total,
        tss_actual=tss_actual,
        workout_type=workout_type,
        session_type_real=session_type_real,
        tsb_after=tsb_after,
        level=level,
    )


# ── Cas de base ───────────────────────────────────────────────────────────────

def test_zero_if_no_tss():
    result = _kpi(tss_actual=None)
    assert result.pts == 0.0
    assert result.score_session == 0.0


def test_zero_if_tss_zero():
    result = _kpi(tss_actual=0.0)
    assert result.pts == 0.0


def test_perfect_session_gives_positive_pts():
    result = _kpi()
    assert result.pts > 0


def test_budget_proportional_to_tss_share():
    # Session 100 TSS dans semaine 400 TSS → 25% du budget semaine
    # Budget semaine = 100/10 = 10 pts
    # Budget séance = 0.25 × 10 = 2.5 pts (avant score)
    result = _kpi(tss_planned=100, week_tss=400, weeks_total=10,
                  tss_actual=100, session_type_real="endurance", workout_type="endurance")
    # score ≈ 1.0 × 1.0 × (1+0) = 1.0 → pts ≈ 2.5
    assert abs(result.pts - 2.5) < 0.1


def test_midplan_linearite():
    """À mi-plan, si toutes les séances scorent 1.0, cumul ≈ 50."""
    n_weeks = 10
    sessions_per_week = 4
    tss_per_session = 100.0
    week_tss = tss_per_session * sessions_per_week

    contributions = []
    for _ in range(n_weeks // 2):           # 5 semaines
        for _ in range(sessions_per_week):  # 4 séances
            r = _kpi(
                tss_planned=tss_per_session,
                week_tss=week_tss,
                weeks_total=n_weeks,
                tss_actual=tss_per_session,
                workout_type="endurance",
                session_type_real="endurance",
                tsb_after=0.0,
            )
            contributions.append(r.pts)

    cumul = compute_cumulative_kpi(contributions)
    assert abs(cumul - 50.0) < 1.0, f"Mi-plan attendu ~50, obtenu {cumul}"


# ── Multiplicateur zone ────────────────────────────────────────────────────────

def test_zone_correct_gives_full_mult():
    r = _kpi(workout_type="intervals", session_type_real="intervals")
    r_none = _kpi(workout_type="intervals", session_type_real=None)
    assert r.pts > r_none.pts  # zones correctes > RPE seul


def test_zone_incorrect_reduces_pts():
    r_correct = _kpi(workout_type="intervals", session_type_real="intervals")
    r_wrong = _kpi(workout_type="intervals", session_type_real="endurance")
    assert r_correct.pts > r_wrong.pts


def test_no_session_type_neutral():
    r = _kpi(session_type_real=None)
    assert r.pts > 0  # pas de pénalité pour absence de classification source
    assert "RPE" in r.reason


# ── Bonus type séance ─────────────────────────────────────────────────────────

def test_long_ride_bonus():
    r_long = _kpi(workout_type="long_ride", session_type_real="long_ride", tss_actual=90, tss_planned=100)
    r_end  = _kpi(workout_type="endurance", session_type_real="endurance",  tss_actual=90, tss_planned=100)
    assert r_long.pts > r_end.pts
    assert "longue" in r_long.reason


def test_intervals_bonus():
    r_int = _kpi(workout_type="intervals", session_type_real="intervals")
    r_end = _kpi(workout_type="endurance", session_type_real="endurance")
    # même budget et même tss_ratio → différence = bonus fractionné
    assert r_int.pts > r_end.pts
    assert "fractionné" in r_int.reason


def test_long_ride_bonus_not_if_tss_ratio_low():
    """Pas de bonus long_ride si TSS réalisé < 85% du planifié."""
    r_low  = _kpi(workout_type="long_ride", session_type_real="long_ride",
                  tss_actual=50, tss_planned=100)  # ratio = 0.5
    r_high = _kpi(workout_type="long_ride", session_type_real="long_ride",
                  tss_actual=90, tss_planned=100)  # ratio = 0.9
    assert "longue" not in r_low.reason
    assert "longue" in r_high.reason


# ── Malus surcharge ────────────────────────────────────────────────────────────

def test_overtraining_gives_negative_pts():
    """Surcharge → contribution négative (seul cas de score négatif)."""
    r = _kpi(tsb_after=-25.0, level="intermediate")  # seuil = -22
    assert r.pts < 0
    assert "surcharge" in r.reason


def test_overtraining_more_severe_more_negative():
    """Plus le TSB est bas, plus la pénalité est forte."""
    r_mild   = _kpi(tsb_after=-24.0, level="intermediate")  # excess faible
    r_severe = _kpi(tsb_after=-35.0, level="intermediate")  # excess fort
    assert r_severe.pts < r_mild.pts


def test_overtraining_threshold_by_level():
    """advanced supporte un TSB plus bas avant malus."""
    tsb = -25.0
    r_inter    = _kpi(tsb_after=tsb, level="intermediate")  # seuil -22 → négatif
    r_advanced = _kpi(tsb_after=tsb, level="advanced")      # seuil -28 → positif
    assert r_inter.pts < 0
    assert r_advanced.pts > 0


def test_no_malus_just_above_threshold():
    r = _kpi(tsb_after=-21.0, level="intermediate")  # seuil = -22
    assert r.pts > 0
    assert "surcharge" not in r.reason


def test_overtraining_capped():
    """La pénalité ne dépasse jamais le budget de la séance."""
    r = _kpi(tsb_after=-100.0, level="intermediate", tss_planned=100, week_tss=400, weeks_total=10)
    # budget = 100/400 × 100/10 = 2.5, pénalité max = -2.5 × 0.5 = -1.25
    assert r.pts >= -2.0  # bien dans les limites


# ── Cumul et utilitaires ──────────────────────────────────────────────────────

def test_cumulative_capped_at_100():
    big = [5.0] * 30  # 150 pts
    assert compute_cumulative_kpi(big) == 100.0


def test_cumulative_empty():
    assert compute_cumulative_kpi([]) == 0.0


def test_kpi_delta_message_format():
    r = KPIContribution(pts=2.3, score_session=1.0, reason="sortie longue ✓ · zones ✓")
    msg = kpi_delta_message(r)
    assert msg.startswith("+2.3 pts")


def test_score_capped_at_1_5():
    """Score ne dépasse pas 1.5 même avec tous les bonus."""
    r = _kpi(
        tss_actual=120,      # ratio 1.2×
        tss_planned=100,
        workout_type="intervals",
        session_type_real="intervals",  # bonus +0.15
        tsb_after=0.0,
    )
    assert r.score_session <= 1.5


# ── Affichage / display ───────────────────────────────────────────────────────

from app.engine.adherence_kpi import compute_kpi_display, KPIDisplayData


def _display(
    cumulative=34.0,
    pts=2.3,
    week_number=4,
    weeks_total=10,
    plan=None,
    logged_slots=None,
) -> KPIDisplayData:
    return compute_kpi_display(
        cumulative=cumulative,
        pts_this_session=pts,
        week_number=week_number,
        weeks_total=weeks_total,
        plan=plan,
        logged_slots=logged_slots,
    )


def test_progress_bar_length():
    d = _display()
    assert len(d.progress_bar) == 20


def test_progress_bar_full_at_100():
    d = _display(cumulative=100.0)
    assert d.progress_bar == "▓" * 20


def test_progress_bar_empty_at_0():
    d = _display(cumulative=0.0)
    assert d.progress_bar == "░" * 20


def test_full_block_contains_score():
    d = _display(cumulative=34.0)
    assert "34.0" in d.full_block
    assert "100" in d.full_block


def test_full_block_contains_delta():
    d = _display(pts=2.3)
    assert "+2.3" in d.full_block


def test_no_milestone_mid_progress():
    d = _display(cumulative=34.0, pts=2.3)
    assert d.milestone is None


def test_milestone_25_triggered():
    # Avant : 23.0, après : 25.5 → franchissement de 25
    d = _display(cumulative=25.5, pts=2.5)
    assert d.milestone is not None
    assert "quart" in d.milestone.lower()


def test_milestone_50_triggered():
    d = _display(cumulative=51.0, pts=3.0)
    assert d.milestone is not None
    assert "mi" in d.milestone.lower()


def test_milestone_75_triggered():
    d = _display(cumulative=76.0, pts=2.0)
    assert d.milestone is not None
    assert "ligne" in d.milestone.lower()


def test_milestone_not_triggered_if_already_past():
    """Pas de milestone si on était déjà au-dessus du seuil avant la séance."""
    # cumul=80, pts=2 → prev=78 → 25/50/75 tous déjà dépassés
    d = _display(cumulative=80.0, pts=2.0)
    assert d.milestone is None


def test_pace_message_in_advance():
    # À S4/10 idéal=40, cumul=47 → +7 → avance
    d = _display(cumulative=47.0, week_number=4, weeks_total=10)
    assert "avance" in d.pace_message


def test_pace_message_on_track():
    # À S4/10 idéal=40, cumul=41 → +1 → dans les temps
    d = _display(cumulative=41.0, week_number=4, weeks_total=10)
    assert "temps" in d.pace_message.lower()


def test_pace_message_big_deficit_no_extra_sessions():
    # Déficit > 15 → message de régularité, pas de session supplémentaire
    d = _display(cumulative=20.0, week_number=6, weeks_total=10)  # idéal=60, delta=-40
    assert "régulier" in d.pace_message.lower()
    assert "extra" not in d.pace_message.lower()
    assert "supplémentaire" not in d.pace_message.lower()
