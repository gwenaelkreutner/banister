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

from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.models.session_log import SessionLog
from app.db.models.wellness import Wellness
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


def test_active_injury_does_not_add_an_alert_to_system_prompt():
    prompt = build_system_prompt(
        first_name="Jean",
        profile=_profile(injury_status={
            "is_injured": True,
            "location": "other",
            "severity": "mild",
            "zone_restrictions": {"Z5": "Z3", "Z6": "Z3"},
        }),
        metrics=None, recent_logs=[], plan=None, today=date(2026, 9, 28),
    )
    assert "BLESSURE ACTIVE" not in prompt
    assert "zones restreintes" not in prompt
    assert "Z5→Z3" not in prompt
    assert "Z6→Z3" not in prompt


@pytest.mark.parametrize("language", ["fr", "en"])
def test_daily_recovery_and_dated_weight_are_in_context(language, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_language", language)
    today = date(2026, 9, 29)
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(weight_kg=80), metrics=None,
        recent_logs=[], plan=None, today=today,
        wellness_today=Wellness(
            date=today, hrv=61, resting_hr=48, sleep_seconds=27000,
            sleep_quality=1, sleep_score=83, fatigue=2, stress=1, mood=1, motivation=1,
        ),
        latest_weight=Wellness(date=today - timedelta(days=2), weight_kg=72.5),
    )
    assert "61 ms" in prompt and "48 bpm" in prompt and "7 h 30 min" in prompt
    assert "83" in prompt and "fatigue 2/4" in prompt
    assert "72.5 kg" in prompt and "2026-09-27" in prompt and "intervals.icu" in prompt
    assert "80.0 kg" not in prompt
    assert "2026-09-29" in prompt


@pytest.mark.parametrize(
    "language,origin", [("fr", "profil configuré"), ("en", "configured profile")]
)
def test_configured_weight_origin_is_explicit(language, origin, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_language", language)
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(weight_kg=80), metrics=None,
        recent_logs=[], plan=None, today=date(2026, 9, 29),
    )
    assert f"80.0 kg ({origin})" in prompt


def test_past_daily_recovery_is_never_shown_as_today():
    prompt = build_system_prompt(
        first_name="Jean", profile=_profile(), metrics=None,
        recent_logs=[], plan=None, today=date(2026, 9, 29),
        wellness_today=Wellness(date=date(2026, 9, 28), hrv=61, resting_hr=48, sleep_seconds=27000),
    )
    assert "61 ms" not in prompt and "48 bpm" not in prompt and "7 h 30 min" not in prompt


async def test_run_chat_reads_paris_daily_values_and_latest_weight(db_session, monkeypatch):
    from app.core import time
    from app.db.models.profile import AthleteProfile
    from app.db.models.user import User
    from app.db.repositories import wellness_repo
    from app.llm import chat, tools

    instant = datetime(2026, 9, 28, 23, 30, tzinfo=UTC)

    class ParisClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    monkeypatch.setattr(time, "datetime", ParisClock)
    monkeypatch.setattr(tools, "datetime", ParisClock)
    assert time.paris_today() == date(2026, 9, 29) != instant.date()

    user = User(telegram_id=791, first_name="Jean")
    db_session.add(user)
    await db_session.flush()
    db_session.add(AthleteProfile(
        user_id=user.id, profile=_profile(weight_kg=80).model_dump(mode="json")
    ))
    await wellness_repo.upsert(
        db_session, user.id, date(2026, 9, 28), ctl=30, atl=40, hrv=99, weight_kg=72.5,
    )
    await wellness_repo.upsert(
        db_session, user.id, date(2026, 9, 29), ctl=65, atl=50,
        hrv=61, resting_hr=48, sleep_seconds=27000,
    )
    # Future weights must not replace the latest known measurement.
    await wellness_repo.upsert(db_session, user.id, date(2026, 9, 30), weight_kg=70)
    await db_session.commit()
    wellness_result = await chat._tool_get_wellness_history(
        {"metrics": ["hrv", "sleep_seconds"], "granularity": "daily"},
        user=user, session=db_session,
    )
    assert wellness_result["range_end"] == "2026-09-29"
    assert wellness_result["points"][-1] == {
        "date": "2026-09-29", "hrv": 61, "sleep_seconds": 27000
    }
    captured = {}

    async def fake_agentic_loop(**kwargs):
        captured.update(kwargs)
        return "Bonjour", None, None, {}, []

    monkeypatch.setattr(chat, "run_agentic_loop", fake_agentic_loop)
    await chat.run_chat("Comment est ma forme ?", user, db_session)
    prompt = captured["system"]
    assert "CTL 65" in prompt and "ATL 50" in prompt
    assert "61.0 ms" in prompt and "48 bpm" in prompt and "7 h 30 min" in prompt
    assert "99.0 ms" not in prompt
    assert "72.5 kg" in prompt and "2026-09-28" in prompt
    assert "80.0 kg" not in prompt and "70.0 kg" not in prompt
    assert "2026-09-29" in prompt

    await wellness_repo.upsert(db_session, user.id, date(2026, 9, 30), ctl=64, atl=45)
    instant = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
    await chat.run_chat("Et aujourd'hui ?", user, db_session)
    prompt = captured["system"]
    assert "61.0 ms" not in prompt and "7 h 30 min" not in prompt
