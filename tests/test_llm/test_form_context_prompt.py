"""FORME ACTUELLE / 7 DERNIÈRES SÉANCES rendering in build_system_prompt() (2026-09-21).

Found live: a bare "TSB +12 ✨ Forme de pointe" label reads as a genuine taper even
right after a training break (both produce a high TSB — the label can't tell them
apart), and its wording collides with the plan's own "Pic de forme" prescriptive phase,
reinforcing the wrong read instead of the detected phase contradicting it. Silent date
gaps between logged sessions and an all-dashes RPE column were also easy for the model
to skim past. Fixed by making the shown data itself honest rather than adding more
prompt rules — see CLAUDE.md § "TSB seul ne suffit pas" / "Sourcer avant de coder".
"""
from __future__ import annotations

from datetime import date

from app.db.models.session_log import SessionLog
from app.engine.atl_ctl import FitnessMetrics
from app.engine.phase_detection import PhaseDetectionResult
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.llm.tools import build_system_prompt


def _profile(**overrides) -> AthleteProfileSchema:
    base = dict(
        objective=ObjectiveProfile(type="fitness", target_date=None),
        availability=AvailabilityProfile(
            hours_per_week=6, preferred_days=["tuesday", "thursday", "saturday"]
        ),
        level="intermediate",
        structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
        physio=PhysioProfile(
            age=34, hr_max=186, hr_max_source="declared",
            hr_rest=58, hr_rest_source="declared",
        ),
        coaching_mode="power",
        health_constraints=False,
    )
    base.update(overrides)
    return AthleteProfileSchema(**base)


def _log(logged_date: date, *, rpe: float | None = None, tss_actual: float = 40.0) -> SessionLog:
    return SessionLog(
        logged_date=logged_date, status="done", tss_actual=tss_actual,
        duration_minutes_actual=60, rpe=rpe,
    )


def test_tsb_shown_as_a_bare_number_without_a_narrative_label():
    """The old `tsb_label()` gloss ("Forme de pointe", "Très frais"...) asserted a
    readiness narrative the number alone can't support — dropped from this prompt."""
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(),
        metrics=FitnessMetrics(atl=22, ctl=34, tsb=12),
        recent_logs=[], plan=None, today=date(2026, 9, 21),
    )
    assert "TSB +12" in prompt
    for narrative in ("Forme de pointe", "Très frais", "Bonne forme", "Surmenage", "✨", "🔴"):
        assert narrative not in prompt


def test_fitness_context_marks_a_stale_source_date():
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(),
        metrics=FitnessMetrics(atl=22, ctl=34, tsb=12),
        recent_logs=[], plan=None, today=date(2026, 9, 21),
        fitness_as_of=date(2026, 9, 19), fitness_is_stale=True,
    )
    assert "Données intervals.icu au 19/09 (pas de donnée plus récente)" in prompt


def test_detected_phase_still_carries_the_qualitative_read():
    phase = PhaseDetectionResult(
        detected_phase="base", confidence="medium", reason_codes=[],
        secondary_phase="peak", streams_agree=False,
    )
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(),
        metrics=FitnessMetrics(atl=22, ctl=34, tsb=12),
        recent_logs=[], plan=None, today=date(2026, 9, 21),
        detected_phase=phase,
    )
    assert "Base aérobie" in prompt
    assert "Pic de forme" in prompt  # secondary/plan-declared phase, shown as disagreement


def test_session_list_annotates_the_gap_since_the_previous_session():
    """A gap invisible to date-arithmetic-by-eye (11 days) must be a stated number, not
    something the model has to compute from two raw dates itself."""
    logs = [
        _log(date(2026, 9, 1)),
        _log(date(2026, 9, 12)),
        _log(date(2026, 9, 19)),
    ]
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(), metrics=None,
        recent_logs=logs, plan=None, today=date(2026, 9, 21),
    )
    assert "01/09" in prompt and "[+" not in prompt.split("01/09")[1].split("\n")[0]
    assert "12/09 [+11j]" in prompt
    assert "19/09 [+7j]" in prompt


def test_rpe_completeness_is_a_stated_fact():
    logs = [
        _log(date(2026, 9, 19), rpe=None),
        _log(date(2026, 9, 20), rpe=5.0),
        _log(date(2026, 9, 21), rpe=None),
    ]
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(), metrics=None,
        recent_logs=logs, plan=None, today=date(2026, 9, 21),
    )
    assert "Ressenti (RPE) renseigné sur 1/3 de ces séances." in prompt


def test_single_recent_session_uses_singular_heading_and_explicit_rpe_label():
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(), metrics=None,
        recent_logs=[_log(date(2026, 9, 21), rpe=5.0)], plan=None, today=date(2026, 9, 21),
    )
    assert "DERNIÈRE SÉANCE :" in prompt
    assert "7 DERNIÈRES SÉANCES" not in prompt
    assert "RPE 5/10 (modéré)" in prompt
    assert "Ressenti (RPE) renseigné" not in prompt


def test_no_recent_logs_still_renders_cleanly():
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(), metrics=None,
        recent_logs=[], plan=None, today=date(2026, 9, 21),
    )
    assert "DERNIÈRE SÉANCE : aucune séance enregistrée." in prompt
    assert "Ressenti (RPE)" not in prompt
