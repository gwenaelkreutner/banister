import pytest
from app.engine.periodization import (
    compute_phase_sequence,
    compute_weekly_tss_targets,
    compute_peak_tss,
)


def test_phase_sequence_short():
    phases = compute_phase_sequence(6)
    total_weeks = sum(p.weeks for p in phases)
    assert total_weeks == 6


def test_phase_sequence_medium():
    phases = compute_phase_sequence(12)
    total_weeks = sum(p.weeks for p in phases)
    assert total_weeks == 12
    phase_names = [p.phase for p in phases]
    assert "taper" in phase_names
    assert "base" in phase_names


def test_phase_sequence_long():
    phases = compute_phase_sequence(24)
    total_weeks = sum(p.weeks for p in phases)
    assert total_weeks == 24
    phase_names = [p.phase for p in phases]
    assert phase_names.count("taper") >= 1
    assert phase_names.count("peak") >= 1


def test_phase_sequence_taper_always_last():
    for n in [4, 8, 12, 20]:
        phases = compute_phase_sequence(n)
        assert phases[-1].phase == "taper", f"Taper doit être la dernière phase ({n} semaines)"


def test_weekly_tss_max_10pct_progression():
    """La règle +10%/semaine doit être respectée sur les semaines non-récup."""
    phases = compute_phase_sequence(12)
    targets = compute_weekly_tss_targets(200, 450, phases)

    prev_tss = None
    for phase, tss, is_recovery in targets:
        if not is_recovery:
            if prev_tss is not None and tss > prev_tss:
                increase_pct = (tss - prev_tss) / prev_tss
                assert increase_pct <= 0.101, (
                    f"Dépassement règle +10%: {prev_tss:.1f} → {tss:.1f} (+{increase_pct*100:.1f}%)"
                )
            prev_tss = tss


def test_recovery_weeks_are_lower():
    phases = compute_phase_sequence(16)
    targets = compute_weekly_tss_targets(200, 500, phases)
    for i, (phase, tss, is_recovery) in enumerate(targets):
        if is_recovery:
            # La semaine de récup doit être significativement plus basse
            assert tss < 350, f"Semaine récup {i+1} trop haute: {tss}"


def test_peak_tss_beginner_cap():
    peak = compute_peak_tss(150, "beginner", False)
    assert peak <= 350


def test_peak_tss_expert_higher():
    peak_beginner = compute_peak_tss(300, "beginner", False)
    peak_expert = compute_peak_tss(300, "expert", True)
    assert peak_expert > peak_beginner


def test_peak_tss_experienced_higher_than_novice():
    peak_no_history = compute_peak_tss(200, "beginner", False)
    peak_with_history = compute_peak_tss(200, "beginner", True)
    assert peak_with_history > peak_no_history
