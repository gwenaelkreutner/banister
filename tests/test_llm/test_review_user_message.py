"""build_review_user_message() (app/llm/prompts.py) — blocs de données optionnels
raw_power/quality_signals (2026-09-21). Purement synchrone, pas de DB/LLM.
"""
from __future__ import annotations

from datetime import date

from app.db.models.session_log import SessionLog
from app.engine.weekly_snapshot import WeeklySnapshot
from app.llm import prompts
from app.services.session_review import ReviewContext


def _snapshot() -> WeeklySnapshot:
    return WeeklySnapshot(
        tss_7d=300.0, tss_6w_avg=280.0, load_trend_pct=7.0,
        sessions_done_7d=4, monotony_index=1.2,
    )


def _ctx(**log_kwargs) -> ReviewContext:
    defaults = dict(
        logged_date=date.today(), status="done", tss_actual=60.0,
        duration_minutes_actual=60,
    )
    defaults.update(log_kwargs)
    log = SessionLog(**defaults)
    return ReviewContext(
        log=log, session_spec=None, fitness_at_session=None,
        weekly_snapshot=_snapshot(), tid=None,
    )


def test_raw_power_block_included_by_default():
    ctx = _ctx(normalized_power=210, intensity_factor=0.82, variability_index=1.03)

    message = prompts.build_review_user_message(ctx)

    assert "Puissance normalisée (NP) : 210 W" in message
    assert "Intensity Factor (IF) : 0.82" in message
    assert "Variability Index (VI) : 1.03" in message


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


def test_blocks_can_be_toggled_off(monkeypatch):
    ctx = _ctx(normalized_power=210, respect_zones_score=87.0)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "raw_power", False)
    monkeypatch.setitem(prompts.REVIEW_DATA_BLOCKS, "quality_signals", False)

    message = prompts.build_review_user_message(ctx)

    assert "Puissance normalisée" not in message
    assert "Respect de la zone cible" not in message
