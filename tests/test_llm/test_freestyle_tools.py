"""spec 009 US1 — the get_freestyle_session_suggestion tool and mode-based tool
filtering (contracts/llm-tool-session-suggestion.md, research.md Decision 6).

Exercises the deterministic tool-dispatch branch directly, the same convention as
tests/test_llm/test_nutrition_tools.py — free-text triggering (does the model call the
right tool) is validated live per quickstart.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.db.models.user import User
from app.db.repositories import profile_repo, session_log_repo, wellness_repo
from app.engine.schemas import (
    AthleteProfileSchema,
    AvailabilityProfile,
    EquipmentProfile,
    ObjectiveProfile,
    PhysioProfile,
)
from app.llm.chat import _tool_get_freestyle_session_suggestion
from app.llm.tools import TOOL_DEFINITIONS, tools_for_mode


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


def _profile(**overrides) -> AthleteProfileSchema:
    base = dict(
        objective=ObjectiveProfile(type="fitness"),
        availability=AvailabilityProfile(hours_per_week=6, preferred_days=["tuesday"]),
        level="intermediate",
        structured_plan_history=True,
        equipment=EquipmentProfile(power_meter=True, ftp=220, ftp_source="declared"),
        physio=PhysioProfile(age=35, hr_max=185, hr_rest=50),
        coaching_mode="power",
        health_constraints=False,
    )
    base.update(overrides)
    return AthleteProfileSchema(**base)


@pytest.mark.parametrize("ctl,expected,zone", [(100, "endurance", "Z2"), (50, "recovery", "Z1")])
async def test_two_free_rides_yesterday_drive_the_actual_suggestion(
    db_session, ctl, expected, zone,
):
    today = date(2026, 9, 29)
    user = await _make_user(db_session, 5201)
    await wellness_repo.upsert(db_session, user.id, today, ctl=ctl, atl=ctl)
    logs = []
    for source_id in ("ride-a", "ride-b"):
        logs.append(await session_log_repo.create(
            db_session, user.id, None, None, None, today - timedelta(days=1), "unplanned",
            tss_actual=80, duration_minutes_actual=120,
            source="intervals_icu", source_activity_id=source_id,
        ))
    imported_copy = SimpleNamespace(
        activity_date=today - timedelta(days=1), tss=80, duration_seconds=7200,
        source="intervals_icu", source_activity_id="ride-a",
    )
    result = await _tool_get_freestyle_session_suggestion(
        {}, user=user, session=db_session, profile=_profile(), logs=logs,
        activities=[imported_copy], today=today,
    )
    assert result["available"]
    assert result["workout_type"] == expected
    assert result["zone_code"] == zone
    assert "7 derniers jours 160 TSS" in result["reasoning_summary"]
    assert "veille : 160 TSS" in result["reasoning_summary"]
    assert result["fitness_as_of"] == "2026-09-29"
    assert result["fitness_is_stale"] is False
    assert result["fitness_source"] == "intervals.icu"
    assert "non évaluable" in result["reasoning_summary"]


async def test_freestyle_reports_stale_source_fitness_and_unknown_load(db_session):
    today = date(2026, 9, 29)
    user = await _make_user(db_session, 5202)
    await wellness_repo.upsert(db_session, user.id, today - timedelta(days=1), ctl=60, atl=50)
    log = SimpleNamespace(logged_date=today, status="unplanned", tss_actual=None)
    result = await _tool_get_freestyle_session_suggestion(
        {}, user=user, session=db_session, profile=_profile(), logs=[log],
        activities=[], today=today,
    )
    assert result["available"]
    assert result["fitness_as_of"] == "2026-09-28"
    assert result["fitness_is_stale"] is True
    assert "anciennes" in result["reasoning_summary"]
    assert "Charge incomplète" in result["reasoning_summary"]


async def test_local_fitness_fallback_uses_the_requested_day(db_session, monkeypatch):
    from app.engine import atl_ctl

    today = date(2026, 9, 29)
    user = await _make_user(db_session, 5203)
    observed = []
    actual_compute = atl_ctl.compute_fitness_from_any

    def compute(items, **kwargs):
        observed.append(kwargs["target_date"])
        return actual_compute(items, **kwargs)

    monkeypatch.setattr(atl_ctl, "compute_fitness_from_any", compute)
    result = await _tool_get_freestyle_session_suggestion(
        {}, user=user, session=db_session, profile=_profile(),
        logs=[SimpleNamespace(logged_date=today, status="unplanned", tss_actual=80,
                              duration_minutes_actual=120)], activities=[], today=today,
    )
    assert observed == [today]
    assert result["available"]
    assert result["fitness_source"] == "local_estimate"
    assert result["fitness_as_of"] is None
    assert "estimée localement" in result["reasoning_summary"]


async def test_chat_and_tool_share_completed_history_and_turn_day(db_session, monkeypatch):
    from app.db.models.activity import Activity
    from app.llm import chat

    today = date(2026, 9, 29)
    monkeypatch.setattr(chat, "paris_today", lambda: today)
    user = await _make_user(db_session, 5204)
    await profile_repo.create(db_session, user.id, _profile().model_dump(mode="json"))
    await wellness_repo.upsert(db_session, user.id, today, ctl=100, atl=100)
    # Today's imported ride used to disappear behind the pre-plan date filter.
    db_session.add(Activity(user_id=user.id, activity_date=today,
                            source="intervals_icu", source_activity_id="ride-a", tss=80,
                            duration_seconds=7200))
    await session_log_repo.create(
        db_session, user.id, None, None, None, today, "unplanned", tss_actual=80,
        duration_minutes_actual=120, source="intervals_icu", source_activity_id="ride-b",
    )
    captured = {}

    async def loop(**kwargs):
        captured["system"] = kwargs["system"]
        # Simulate midnight passing during the model call: the tool must keep the turn date.
        monkeypatch.setattr(chat, "paris_today", lambda: today + timedelta(days=1))
        result = await kwargs["tool_executor"]("get_freestyle_session_suggestion", {})
        captured["result"] = result
        return "Une séance facile.", "get_freestyle_session_suggestion", result, {}, [
            {"name": "get_freestyle_session_suggestion", "args": {}, "result": result},
        ]

    monkeypatch.setattr(chat, "run_agentic_loop", loop)
    _, _, _, proposal, _, _ = await chat.run_chat("Que faire aujourd'hui ?", user, db_session)
    assert "7 jours : 2 séances, 160 TSS" in captured["system"]
    assert "7 derniers jours 160 TSS" in captured["result"]["reasoning_summary"]
    assert proposal["workout_type"] == "endurance"
    assert proposal["fitness_as_of"] == str(today)
    assert proposal["fitness_is_stale"] is False


async def test_freestyle_reuses_measured_recovery_signal_for_selection_and_picker(
    db_session, monkeypatch,
):
    from app.engine.guardrail_thresholds import BASELINE_MIN_SAMPLES
    from app.llm import template_picker

    today = date(2026, 9, 29)
    user = await _make_user(db_session, 5205)
    for i in range(BASELINE_MIN_SAMPLES):
        await wellness_repo.upsert(
            db_session, user.id, today - timedelta(days=2 + i), resting_hr=50,
        )
    await wellness_repo.upsert(db_session, user.id, today - timedelta(days=1), resting_hr=56)
    await wellness_repo.upsert(db_session, user.id, today, ctl=60, atl=45, resting_hr=57)
    seen = []

    async def pick(preference, candidates):
        seen.extend(candidate.workout_type for candidate in candidates)
        return None

    monkeypatch.setattr(template_picker, "pick_template", pick)
    result = await _tool_get_freestyle_session_suggestion(
        {"style_preference": "facile et régulière"}, user=user, session=db_session,
        profile=_profile(), logs=[], activities=[], today=today,
    )
    assert result["available"]
    assert result["workout_type"] == "recovery"
    assert set(seen) == {"recovery"}
    assert "rhr_high" in result["reasoning_summary"]


class TestGetFreestyleSessionSuggestion:
    async def test_insufficient_history_returns_unavailable(self, db_session):
        """FR-011: no wellness, no activities, no logs — never guess a session."""
        user = await _make_user(db_session, 5001)

        result = await _tool_get_freestyle_session_suggestion(
            {}, user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is False
        assert "reason" in result

    async def test_no_profile_returns_unavailable(self, db_session):
        user = await _make_user(db_session, 5002)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=50)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {}, user=user, session=db_session, profile=None, logs=[], activities=[],
        )

        assert result["available"] is False

    async def test_available_with_wellness_history_returns_concrete_session(self, db_session):
        user = await _make_user(db_session, 5003)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=50)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {}, user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["workout_type"] in {"long_ride", "intervals", "endurance", "recovery"}
        assert result["duration_minutes"] > 0
        assert result["target_tss"] > 0
        assert "zone_code" in result
        assert result["reasoning_summary"]

    async def test_respects_disliked_workout_types_from_athlete_notes(self, db_session):
        """FR-004: a preference stored via update_coach_memory's athlete_notes key is
        honored by the freestyle suggestion."""
        user = await _make_user(db_session, 5004)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()
        profile_orm = await profile_repo.create(db_session, user.id, {})
        await profile_repo.update_athlete_notes(
            db_session, profile_orm, {"disliked_workout_types": "intervals,long_ride"}
        )
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {}, user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["workout_type"] not in {"intervals", "long_ride"}


class TestFreestyleSessionNegotiation:
    """spec 011 — requested_workout_type/max_duration_minutes/template_id threaded from
    the tool's `args` through to app/engine/freestyle_selector.py, unmodified."""

    async def test_requested_workout_type_is_honored(self, db_session):
        """spec 011 US1 Acceptance Scenario 1."""
        user = await _make_user(db_session, 5010)
        # Low CTL/high ATL → negative TSB → would otherwise default to recovery/endurance.
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=90)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"requested_workout_type": "intervals"},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["workout_type"] == "intervals"
        assert "spontanément" in result["reasoning_summary"]

    async def test_requested_workout_type_overrides_disliked_note(self, db_session):
        """spec 011 FR-009: the explicit ask wins for this one suggestion; the standing
        preference itself is left untouched (not asserted here — see its own test)."""
        user = await _make_user(db_session, 5011)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()
        profile_orm = await profile_repo.create(db_session, user.id, {})
        await profile_repo.update_athlete_notes(
            db_session, profile_orm, {"disliked_workout_types": "intervals,long_ride"}
        )
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"requested_workout_type": "intervals"},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["workout_type"] == "intervals"

    def test_tool_schema_type_enum_has_only_the_four_supported_types(self):
        """spec 011 FR-008 pinned at the schema boundary: an unsupported type (e.g.
        'yoga') can never be submitted as a valid tool argument in the first place."""
        tool = next(
            t for t in TOOL_DEFINITIONS
            if t["function"]["name"] == "get_freestyle_session_suggestion"
        )
        enum = tool["function"]["parameters"]["properties"]["requested_workout_type"]["enum"]
        assert set(enum) == {"long_ride", "intervals", "endurance", "recovery"}

    def test_tool_schema_carries_no_template_catalogue(self):
        """The template catalogue must never ride along in the tool schema — it is
        resolved by a second, isolated LLM call (app/llm/template_picker.py) only when
        the athlete voiced a style preference."""
        tool = next(
            t for t in TOOL_DEFINITIONS
            if t["function"]["name"] == "get_freestyle_session_suggestion"
        )
        props = tool["function"]["parameters"]["properties"]
        assert "template_id" not in props
        assert "enum" not in props["style_preference"]
        assert len(props["style_preference"]["description"]) < 600

    async def test_style_preference_is_resolved_by_picker_within_resolved_type(
        self, db_session, monkeypatch
    ):
        """The picker only ever sees candidates of the workout type already resolved by
        the engine, and its answer is threaded through as `requested_template_id`."""
        import app.llm.template_picker as picker_mod

        seen: dict = {}

        async def fake_pick(style_preference, candidates):
            seen["preference"] = style_preference
            seen["types"] = {t.workout_type for t in candidates}
            return "vo2-5x5"

        # chat.py imports pick_template at call time, so patching the module attribute is enough.
        monkeypatch.setattr(picker_mod, "pick_template", fake_pick)

        user = await _make_user(db_session, 5012)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"requested_workout_type": "intervals", "style_preference": "des efforts de 5 min"},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["workout_type"] == "intervals"
        assert seen["preference"] == "des efforts de 5 min"
        assert seen["types"] == {"intervals"}

    async def test_style_preference_picker_failure_falls_back_to_rotation(
        self, db_session, monkeypatch
    ):
        """A picker that declines (None) must never break the suggestion."""
        import app.llm.template_picker as picker_mod

        async def fake_pick(style_preference, candidates):
            return None

        monkeypatch.setattr(picker_mod, "pick_template", fake_pick)

        user = await _make_user(db_session, 5015)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"style_preference": "du steady"},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True

    async def test_max_duration_minutes_bounds_the_result(self, db_session):
        """spec 011 US3 Acceptance Scenario 1."""
        user = await _make_user(db_session, 5013)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"max_duration_minutes": 90},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["duration_minutes"] <= 90

    async def test_requested_duration_minutes_is_honored_and_warns_when_heavier(
        self, db_session
    ):
        """"Je veux rouler 1h30" is a requested duration, not availability."""
        user = await _make_user(db_session, 5016)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"requested_duration_minutes": 90},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is True
        assert result["duration_minutes"] == 90
        assert result["duration_warning"]

    async def test_max_duration_minutes_too_tight_is_reported_as_unavailable(self, db_session):
        """spec 011 US3 Acceptance Scenario 2: never an over-length session."""
        user = await _make_user(db_session, 5014)
        await wellness_repo.upsert(db_session, user.id, date.today(), ctl=60, atl=45)
        await db_session.commit()

        result = await _tool_get_freestyle_session_suggestion(
            {"max_duration_minutes": 5},
            user=user, session=db_session, profile=_profile(), logs=[], activities=[],
        )

        assert result["available"] is False
        assert "reason" in result


class TestToolsForMode:
    def test_freestyle_tool_only_offered_in_freestyle_mode(self):
        freestyle_tools = {t["function"]["name"] for t in tools_for_mode("freestyle")}
        goal_tools = {t["function"]["name"] for t in tools_for_mode("goal")}

        assert "get_freestyle_session_suggestion" in freestyle_tools
        assert "get_freestyle_session_suggestion" not in goal_tools

    def test_plan_only_tools_absent_in_freestyle_mode(self):
        freestyle_tools = {t["function"]["name"] for t in tools_for_mode("freestyle")}

        for plan_only in (
            "get_upcoming_sessions", "propose_plan_modification", "propose_session_adjustment",
        ):
            assert plan_only not in freestyle_tools

    def test_plan_only_tools_present_in_goal_mode(self):
        goal_tools = {t["function"]["name"] for t in tools_for_mode("goal")}

        for plan_only in (
            "get_upcoming_sessions", "propose_plan_modification", "propose_session_adjustment",
        ):
            assert plan_only in goal_tools

    def test_filtering_never_drops_an_unrelated_tool(self):
        """Only the two mode-specific tool sets are affected — everything else (meal
        logging, coach memory, injury status) stays available in both modes."""
        freestyle_tools = {t["function"]["name"] for t in tools_for_mode("freestyle")}
        goal_tools = {t["function"]["name"] for t in tools_for_mode("goal")}
        all_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        mode_specific = {
            "get_upcoming_sessions", "propose_plan_modification",
            "propose_session_adjustment", "get_freestyle_session_suggestion",
        }

        for name in all_names - mode_specific:
            assert name in freestyle_tools
            assert name in goal_tools
