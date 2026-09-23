"""Contracts for bounded coaching-data tools and compact hot-context facts."""
from datetime import date, datetime
from types import SimpleNamespace

from app.llm.tools import (
    PARALLEL_READ_TOOLS,
    TOOL_DEFINITIONS,
    _temporal_reference_rules,
    tools_for_mode,
)
from app.services.coach_queries import hot_training_summary


def test_coach_data_tools_are_available_in_both_modes():
    names = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
    expected = {
        "get_fitness_history",
        "get_training_trend",
        "get_session_detail",
        "get_wellness_history",
    }
    assert expected <= names
    for mode in ("goal", "freestyle"):
        assert expected <= {tool["function"]["name"] for tool in tools_for_mode(mode)}


def test_new_coach_data_tools_are_safe_to_run_in_one_read_wave():
    assert {
        "get_fitness_history",
        "get_training_trend",
        "get_session_detail",
        "get_wellness_history",
    } <= PARALLEL_READ_TOOLS


def test_plan_tool_supports_a_bounded_window():
    tool = next(tool for tool in TOOL_DEFINITIONS if tool["function"]["name"] == "get_upcoming_sessions")
    properties = tool["function"]["parameters"]["properties"]
    assert properties["days"]["maximum"] == 42
    assert properties["start_offset"]["minimum"] == -7
    assert properties["start_offset"]["maximum"] == 56


def test_hot_training_summary_stays_compact_and_uses_deterministic_totals():
    items = [
        SimpleNamespace(logged_date=date(2026, 9, 21), tss_actual=45, rpe=5),
        SimpleNamespace(logged_date=date(2026, 9, 1), tss_actual=70, rpe=None),
    ]
    summary = hot_training_summary(items, today=date(2026, 9, 22))

    assert summary == ["7 jours : 1 séances, 45 TSS, RPE 1/1", "28 jours : 2 séances, 115 TSS"]


def test_temporal_reference_rules_bind_all_relative_dates_to_the_current_turn():
    rules = "\n".join(_temporal_reference_rules(datetime(2026, 9, 23, 20, 11)))

    assert "mercredi 23 septembre 2026, 20:11 à Paris (2026-09-23)" in rules
    assert "toute date relative" in rules
    assert "jamais par rapport au calendrier du plan" in rules
    assert "date ISO envoyée" in rules
