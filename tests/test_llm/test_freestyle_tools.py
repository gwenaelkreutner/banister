"""spec 009 US1 — the get_freestyle_session_suggestion tool and mode-based tool
filtering (contracts/llm-tool-session-suggestion.md, research.md Decision 6).

Exercises the deterministic tool-dispatch branch directly, the same convention as
tests/test_llm/test_nutrition_tools.py — free-text triggering (does the model call the
right tool) is validated live per quickstart.md.
"""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import profile_repo, wellness_repo
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
