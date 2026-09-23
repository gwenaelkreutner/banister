"""build_review_user_message() (app/llm/prompts.py) — blocs de données optionnels
raw_power/quality_signals/environmental/form_context/nutrition_context (2026-09-21).
Purement synchrone, pas de DB/LLM.
"""
from __future__ import annotations

from datetime import date

from app.db.models.session_log import SessionLog
from app.engine.atl_ctl import FitnessMetrics
from app.engine.phase_detection import PhaseDetectionResult
from app.engine.weekly_snapshot import WeeklySnapshot
from app.llm import prompts
from app.services.session_review import ReviewContext


def _snapshot() -> WeeklySnapshot:
    return WeeklySnapshot(
        tss_7d=300.0, tss_6w_avg=280.0, load_trend_pct=7.0,
        sessions_done_7d=4, monotony_index=1.2,
    )


def _ctx(
    recovery_index=None, detected_phase=None,
    hydration_volume_l=None, kcal_consumed=None,
    **log_kwargs,
) -> ReviewContext:
    defaults = dict(
        logged_date=date.today(), status="done", tss_actual=60.0,
        duration_minutes_actual=60,
    )
    defaults.update(log_kwargs)
    log = SessionLog(**defaults)
    return ReviewContext(
        log=log, session_spec=None, fitness_at_session=None,
        weekly_snapshot=_snapshot(), tid=None,
        recovery_index=recovery_index, detected_phase=detected_phase,
        hydration_volume_l=hydration_volume_l, kcal_consumed=kcal_consumed,
    )


def test_raw_power_block_included_by_default():
    ctx = _ctx(normalized_power=210, intensity_factor=0.82, variability_index=1.03)

    message = prompts.build_review_user_message(ctx)

    assert "Puissance normalisée (NP) : 210 W" in message
    assert "Intensity Factor (IF) : 0.82" in message
    assert "Variability Index (VI) : 1.03" in message


def test_freestyle_review_declares_the_absence_of_a_plan_and_tsb_gloss():
    ctx = _ctx()
    ctx.fitness_at_session = FitnessMetrics(ctl=42.0, atl=29.0, tsb=13.0)

    message = prompts.build_review_user_message(ctx)

    assert "sortie en mode libre, sans séance planifiée ni cible à évaluer" in message
    assert "TSB +13, CTL 42, ATL 29" in message
    assert "Forme de pointe" not in message


def test_freestyle_review_prompt_forbids_plan_language_and_requires_explanations():
    prompt = prompts.build_review_system_prompt(has_rpe=True, coaching_mode="freestyle")

    assert "CADRE MODE LIBRE" in prompt
    assert "Ne parle jamais de séance prévue" in prompt
    assert "Interprète les métriques : ne les récite jamais" in prompt
    assert "TSB positif décrit de la fraîcheur relative" in prompt
    for heading in (
        "**Lecture de l'effort**",
        "**Pacing et réponse physiologique**",
        "**Ressenti et cohérence**",
        "**Forme et charge**",
        "**Bilan**",
    ):
        assert heading in prompt


def test_variability_index_ignored_under_30_minutes():
    ctx = _ctx(duration_minutes_actual=20, variability_index=1.15)

    message = prompts.build_review_user_message(ctx)

    assert "Variability Index" not in message


def test_quality_signals_block_included_by_default():
    ctx = _ctx(
        respect_zones_score=87.0, cardiac_drift_index=0.064,
        intervals_consistency_index=0.91,
    )

    message = prompts.build_review_user_message(ctx)

    assert "Respect de la zone cible : 87/100" in message
    assert "Dérive cardiaque : +6.4%" in message
    assert "Consistance des intervalles : 91%" in message


def test_environmental_block_included_by_default():
    ctx = _ctx(elevation_gain_m=509.0, average_temp_c=26.2, kilojoules=1049.2)

    message = prompts.build_review_user_message(ctx)

    assert "Dénivelé : 509 m" in message
    assert "Température moyenne : 26°C" in message
    assert "Énergie dépensée : 1049 kJ" in message


def test_form_context_block_included_by_default():
    phase = PhaseDetectionResult(
        detected_phase="build", confidence="medium", reason_codes=["load_trend_+22pct_rising"],
        secondary_phase="peak", streams_agree=False,
    )
    ctx = _ctx(recovery_index=0.87, detected_phase=phase)

    message = prompts.build_review_user_message(ctx)

    assert "Indice de récupération ce jour-là : 0.87" in message
    assert (
        "Phase détectée (comportement récent) : Construction (plan déclare : Pic de forme)"
        in message
    )


def test_nutrition_context_block_included_by_default():
    ctx = _ctx(kcal_consumed=2400, hydration_volume_l=2.5)

    message = prompts.build_review_user_message(ctx)

    assert "Calories mangées ce jour-là (source intervals.icu) : 2400 kcal" in message
    assert "Eau bue ce jour-là (source intervals.icu) : 2.5 L" in message


def test_blocks_can_be_toggled_off(monkeypatch):
    phase = PhaseDetectionResult(
        detected_phase="base", confidence="low", reason_codes=[],
        secondary_phase=None, streams_agree=None,
    )
    ctx = _ctx(
        normalized_power=210, respect_zones_score=87.0, elevation_gain_m=509.0,
        recovery_index=0.87, detected_phase=phase,
        kcal_consumed=2400, hydration_volume_l=2.5,
    )
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "raw_power", False)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "quality_signals", False)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "environmental", False)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "form_context", False)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "nutrition_context", False)

    message = prompts.build_review_user_message(ctx)

    assert "Puissance normalisée" not in message
    assert "Respect de la zone cible" not in message
    assert "Dénivelé" not in message
    assert "Indice de récupération" not in message
    assert "Phase détectée" not in message
    assert "Calories mangées" not in message
    assert "Eau bue" not in message
