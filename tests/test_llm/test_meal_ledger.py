"""Tests for the deterministic nutrition confirmation (`_format_meal_ledger`).

The chat's own free-text reply about what it saved isn't trustworthy on its own — a real
conversation surfaced both a message that never called `log_meal` at all (silent no-op
dressed as a diary entry) and one that logged 2 of 3 described items without saying so
clearly. `_format_meal_ledger` builds a minimalist confirmation from the actual
`log_meal`/`undo_last_meal_entry` tool results captured in `tool_calls_log`, never from
LLM prose — sent as a separate Telegram message by
`app/bot/routers/chat.py::_send_meal_log`, only when a nutrition tool actually ran this
turn. Format (user-chosen, 2026-09-21): "✅ Enregistré — item (cal), item (cal)" grouped
per date, "🗑️ Supprimé"/"❌ Non enregistré" per event, "🧾 Total du JJ/MM : N cal" per date.
"""
from __future__ import annotations

from app.llm.chat import _format_meal_ledger


def test_no_nutrition_tool_calls_returns_none():
    assert _format_meal_ledger([]) is None
    assert _format_meal_ledger([
        {"name": "get_upcoming_sessions", "args": {}, "result": {"sessions": []}},
    ]) is None


def test_successful_meal_log_is_reflected_with_slot_label_and_day_total():
    ledger = _format_meal_ledger([
        {
            "name": "log_meal",
            "args": {"entry_type": "meal", "meal_slot": "lunch", "estimated_calories": 850},
            "result": {
                "ok": True,
                "estimated_calories": 850,
                "entry_date": "2026-09-21",
                "day_total_estimated_calories": 1160,
                "replaced_existing_entries": False,
            },
        },
    ])
    assert ledger is not None
    assert "✅ Enregistré — déjeuner (850 cal)" in ledger
    assert "🧾 Total du 21/09 : 1160 cal" in ledger


def test_multiple_logged_items_same_day_grouped_on_one_line():
    ledger = _format_meal_ledger([
        {
            "name": "log_meal",
            "args": {"entry_type": "meal", "meal_slot": "breakfast", "estimated_calories": 310},
            "result": {
                "ok": True, "estimated_calories": 310, "entry_date": "2026-09-21",
                "day_total_estimated_calories": 310, "replaced_existing_entries": False,
            },
        },
        {
            "name": "log_meal",
            "args": {"entry_type": "meal", "meal_slot": "lunch", "estimated_calories": 850},
            "result": {
                "ok": True, "estimated_calories": 850, "entry_date": "2026-09-21",
                "day_total_estimated_calories": 1160, "replaced_existing_entries": False,
            },
        },
    ])
    assert ledger is not None
    # Grouped into a single "✅ Enregistré" line for the shared date, not one per item.
    assert ledger.count("✅ Enregistré") == 1
    assert "petit-déjeuner (310 cal)" in ledger
    assert "déjeuner (850 cal)" in ledger
    # Only the last call's day total is kept per date — it's already the cumulative one.
    assert ledger.count("🧾 Total du") == 1
    assert "🧾 Total du 21/09 : 1160 cal" in ledger


def test_failed_log_meal_is_shown_as_not_saved_not_hallucinated_as_success():
    ledger = _format_meal_ledger([
        {
            "name": "log_meal",
            "args": {"entry_type": "meal", "estimated_calories": 50000},
            "result": {"ok": False, "error": "estimation calorique hors limites plausibles"},
        },
    ])
    assert ledger is not None
    assert "❌ Non enregistré — estimation calorique hors limites plausibles" in ledger
    assert "✅" not in ledger
    assert "🧾" not in ledger


def test_day_recap_labeled_distinctly():
    ledger = _format_meal_ledger([
        {
            "name": "log_meal",
            "args": {"entry_type": "day_recap", "estimated_calories": 2000},
            "result": {
                "ok": True, "estimated_calories": 2000, "entry_date": "2026-09-21",
                "day_total_estimated_calories": 2000, "replaced_existing_entries": True,
            },
        },
    ])
    assert ledger is not None
    assert "récap journée (2000 cal)" in ledger


def test_successful_undo_shown_with_removed_calories_and_new_total():
    ledger = _format_meal_ledger([
        {
            "name": "undo_last_meal_entry",
            "args": {},
            "result": {
                "ok": True, "removed_estimated_calories": 900,
                "entry_date": "2026-09-21", "day_total_estimated_calories": 500,
            },
        },
    ])
    assert ledger is not None
    assert "🗑️ Supprimé — -900 cal" in ledger
    assert "🧾 Total du 21/09 : 500 cal" in ledger


def test_failed_undo_shown_as_nothing_to_undo():
    ledger = _format_meal_ledger([
        {
            "name": "undo_last_meal_entry",
            "args": {},
            "result": {"ok": False, "error": "aucune entrée aujourd'hui à annuler"},
        },
    ])
    assert ledger is not None
    assert "❌ Non enregistré — aucune entrée aujourd'hui à annuler" in ledger


def test_non_nutrition_tool_calls_alongside_a_meal_log_are_ignored():
    ledger = _format_meal_ledger([
        {"name": "get_upcoming_sessions", "args": {}, "result": {"sessions": []}},
        {
            "name": "log_meal",
            "args": {"entry_type": "meal", "meal_slot": "snack", "estimated_calories": 200},
            "result": {
                "ok": True, "estimated_calories": 200, "entry_date": "2026-09-21",
                "day_total_estimated_calories": 200, "replaced_existing_entries": False,
            },
        },
    ])
    assert ledger is not None
    assert ledger.count("✅ Enregistré") == 1
    assert "collation (200 cal)" in ledger
