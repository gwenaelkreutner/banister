"""Tool-dispatch tests for daily calorie tracking (spec 008).

Exercises the deterministic branches of the three nutrition tools directly — bounds
validation, day-recap replace semantics, and the history tool's "logged" shape — per
contracts/nutrition-tools.md. Repository-level correctness (aggregation, cascade) is
covered separately in tests/test_db/test_meal_entries.py; the free-text extraction itself
(does the model call the right tool) is validated live per quickstart.md Scenario 1.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.models.user import User
from app.db.repositories import meal_entry_repo
from app.llm.chat import _tool_get_calorie_history, _tool_log_meal, _tool_undo_last_meal_entry


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


# ── log_meal ───────────────────────────────────────────────────────────────────


async def test_log_meal_persists_and_returns_deterministic_day_total(db_session):
    user = await _make_user(db_session, 4001)
    result = await _tool_log_meal(
        {"entry_type": "meal", "meal_slot": "lunch", "estimated_calories": 650},
        user=user, session=db_session, raw_message="3 œufs, du pain, une pomme",
    )
    await db_session.commit()

    assert result["ok"] is True
    assert result["estimated_calories"] == 650
    assert result["day_total_estimated_calories"] == 650
    assert result["replaced_existing_entries"] is False

    # The raw athlete message is stored verbatim, not an LLM paraphrase (research R1).
    totals = await meal_entry_repo.daily_totals(db_session, user.id, date.today(), date.today())
    assert totals[0].total_calories == 650

    # A second entry the same day: the returned total is the deterministic sum, not
    # something the tool (or the model) re-adds itself (research R2).
    result2 = await _tool_log_meal(
        {"entry_type": "meal", "meal_slot": "dinner", "estimated_calories": 800},
        user=user, session=db_session, raw_message="riz, poulet, brocolis",
    )
    await db_session.commit()
    assert result2["day_total_estimated_calories"] == 1450


async def test_log_meal_rejects_implausible_calories_without_writing(db_session):
    user = await _make_user(db_session, 4002)

    for bad_value in (0, -50, 8001, 50000):
        result = await _tool_log_meal(
            {"entry_type": "meal", "estimated_calories": bad_value},
            user=user, session=db_session, raw_message="texte quelconque",
        )
        assert result["ok"] is False
        assert "error" in result

    totals = await meal_entry_repo.daily_totals(db_session, user.id, date.today(), date.today())
    assert totals == []


async def test_log_meal_days_ago_clamped_and_applied(db_session):
    user = await _make_user(db_session, 4003)
    yesterday = date.today() - timedelta(days=1)

    result = await _tool_log_meal(
        {"entry_type": "meal", "estimated_calories": 500, "days_ago": 1},
        user=user, session=db_session, raw_message="hier soir j'ai mangé des pâtes",
    )
    await db_session.commit()

    assert result["entry_date"] == str(yesterday)
    totals = await meal_entry_repo.daily_totals(db_session, user.id, yesterday, yesterday)
    assert totals[0].total_calories == 500


# ── log_meal: day_recap replace semantics (US2) ───────────────────────────────


async def test_log_meal_day_recap_replaces_existing_meal_entries(db_session):
    user = await _make_user(db_session, 4004)
    today = date.today()

    await _tool_log_meal(
        {"entry_type": "meal", "meal_slot": "lunch", "estimated_calories": 600},
        user=user, session=db_session, raw_message="déjeuner",
    )
    await db_session.commit()
    await _tool_log_meal(
        {"entry_type": "meal", "meal_slot": "dinner", "estimated_calories": 700},
        user=user, session=db_session, raw_message="dîner",
    )
    await db_session.commit()

    result = await _tool_log_meal(
        {"entry_type": "day_recap", "estimated_calories": 2000},
        user=user, session=db_session, raw_message="au final j'ai mangé environ 2000 calories",
    )
    await db_session.commit()

    assert result["replaced_existing_entries"] is True
    # The total is the recap alone — NOT the recap summed with the replaced entries
    # (the exact double-counting failure FR-009 forbids).
    assert result["day_total_estimated_calories"] == 2000

    totals = await meal_entry_repo.daily_totals(db_session, user.id, today, today)
    assert len(totals) == 1
    assert totals[0].entry_count == 1
    assert totals[0].total_calories == 2000


# ── undo_last_meal_entry (US4) ─────────────────────────────────────────────────


async def test_undo_last_meal_entry_removes_latest_and_recomputes_total(db_session):
    user = await _make_user(db_session, 4005)

    await _tool_log_meal(
        {"entry_type": "meal", "estimated_calories": 500},
        user=user, session=db_session, raw_message="premier",
    )
    await db_session.commit()
    await _tool_log_meal(
        {"entry_type": "meal", "estimated_calories": 900},
        user=user, session=db_session, raw_message="deuxieme, une erreur",
    )
    await db_session.commit()

    result = await _tool_undo_last_meal_entry(user=user, session=db_session)
    await db_session.commit()

    assert result["ok"] is True
    assert result["removed_estimated_calories"] == 900
    assert result["day_total_estimated_calories"] == 500


async def test_undo_last_meal_entry_reports_nothing_to_undo(db_session):
    user = await _make_user(db_session, 4006)
    result = await _tool_undo_last_meal_entry(user=user, session=db_session)
    assert result["ok"] is False
    assert "error" in result


# ── get_calorie_history (US3) ───────────────────────────────────────────────────


async def test_get_calorie_history_shows_every_day_logged_or_not(db_session):
    user = await _make_user(db_session, 4007)
    today = date.today()

    # Log something 2 days ago (within the 3-day window queried) and leave the rest empty.
    await _tool_log_meal(
        {"entry_type": "meal", "estimated_calories": 500, "days_ago": 2},
        user=user, session=db_session, raw_message="avant-hier",
    )
    await db_session.commit()

    result = await _tool_get_calorie_history({"days": 3}, user=user, session=db_session)

    assert result["days"][0]["date"] == str(today - timedelta(days=2))
    assert result["days"][0]["logged"] is True
    assert result["days"][0]["total_calories"] == 500
    assert result["days"][0]["entry_count"] == 1

    assert result["days"][1]["logged"] is False
    assert "total_calories" not in result["days"][1]

    assert result["days"][2]["date"] == str(today)
    assert result["days"][2]["logged"] is False
