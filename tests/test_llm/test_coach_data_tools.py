"""Contracts for bounded coaching-data tools and compact hot-context facts."""
from datetime import date, datetime, timedelta
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
    tool = next(
        tool for tool in TOOL_DEFINITIONS if tool["function"]["name"] == "get_upcoming_sessions"
    )
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


async def test_training_queries_and_hot_context_share_deduplicated_history(db_session, monkeypatch):
    from app.db.models.activity import Activity
    from app.db.models.user import User
    from app.db.repositories import session_log_repo
    from app.services import coach_queries

    today = date(2026, 9, 29)
    monkeypatch.setattr(coach_queries, "paris_today", lambda: today)
    user = User(telegram_id=793)
    db_session.add(user)
    await db_session.flush()
    log = await session_log_repo.create(
        db_session, user.id, None, None, None, today, "unplanned", tss_actual=80,
        duration_minutes_actual=120, source="intervals_icu", source_activity_id="ride-a",
    )
    copies = [Activity(
        user_id=user.id, activity_date=today, source="intervals_icu",
        source_activity_id=source_id, tss=80, duration_seconds=7200,
    ) for source_id in ("ride-a", "ride-b")]
    db_session.add_all(copies)
    trend = await coach_queries.training_trend(db_session, user.id, days=7)
    assert trend["weeks"] == [{
        "week_start": "2026-09-28", "sessions": 2, "tss": 160, "minutes": 240,
    }]
    detail = await coach_queries.session_detail(db_session, user.id, session_date=today)
    assert len(detail["sessions"]) == 2
    assert sum(row["tss"] for row in detail["sessions"]) == 160
    assert hot_training_summary([*copies, log], today=today)[0] == (
        "7 jours : 2 séances, 160 TSS, RPE 0/2"
    )


def test_temporal_reference_rules_bind_all_relative_dates_to_the_current_turn():
    rules = "\n".join(_temporal_reference_rules(datetime(2026, 9, 23, 20, 11)))

    assert "mercredi 23 septembre 2026, 20:11 à Paris (2026-09-23)" in rules
    assert "toute date relative" in rules
    assert "jamais par rapport au calendrier du plan" in rules
    assert "date ISO envoyée" in rules


async def test_wellness_tool_exposes_all_synced_fields_in_bounded_calls(db_session, monkeypatch):
    from app.db.models.user import User
    from app.db.models.wellness import Wellness
    from app.db.repositories import wellness_repo
    from app.llm.chat import _tool_get_wellness_history
    from app.services import coach_queries

    today = date(2026, 9, 29)
    monkeypatch.setattr(coach_queries, "paris_today", lambda: today)
    user = User(telegram_id=792, first_name="Jean")
    db_session.add(user)
    await db_session.flush()
    metrics = coach_queries.WELLNESS_METRICS
    fields = set(Wellness.__table__.columns.keys()) - {
        "id", "user_id", "date", "created_at", "updated_at"
    }
    assert set(metrics) == fields
    values = {m: ("LUTEAL" if m.startswith("menstrual_phase") else 42) for m in metrics}
    await wellness_repo.upsert(db_session, user.id, today, **values)
    await wellness_repo.upsert(db_session, user.id, today - timedelta(days=90), sleep_seconds=1)
    schema = next(
        t["function"] for t in TOOL_DEFINITIONS
        if t["function"]["name"] == "get_wellness_history"
    )
    assert set(schema["parameters"]["properties"]["metrics"]["items"]["enum"]) == fields
    assert schema["parameters"]["properties"]["metrics"]["maxItems"] == 4
    assert schema["parameters"]["properties"]["days"]["maximum"] == 90

    for offset in range(0, len(metrics), 4):
        selected = list(metrics[offset:offset + 4])
        result = await _tool_get_wellness_history(
            {"days": 999, "metrics": selected, "granularity": "daily"},
            user=user, session=db_session,
        )
        assert result["metrics"] == selected
        assert result["range_start"] == str(today - timedelta(days=89))
        assert result["range_end"] == str(today)
        assert result["points"] == [{"date": str(today), **{m: values[m] for m in selected}}]

    result = await _tool_get_wellness_history(
        {"days": 90, "metrics": [
            "id", "sleep_seconds", "sleep_seconds", "spo2", "systolic", "steps", "fat_g"
        ]},
        user=user, session=db_session,
    )
    assert result["metrics"] == ["sleep_seconds", "spo2", "systolic", "steps"]
    result = await _tool_get_wellness_history(
        {"metrics": ["user_id"]}, user=user, session=db_session,
    )
    assert result["metrics"] == ["hrv", "resting_hr"]


async def test_wellness_history_preserves_unknown_daily_readings(db_session, monkeypatch):
    from app.db.models.user import User
    from app.db.repositories import wellness_repo
    from app.services import coach_queries

    today = date(2026, 9, 29)
    monkeypatch.setattr(coach_queries, "paris_today", lambda: today)
    user = User(telegram_id=793)
    db_session.add(user)
    await db_session.flush()
    await wellness_repo.upsert(db_session, user.id, today - timedelta(days=1), sleep_seconds=27000)
    await wellness_repo.upsert(db_session, user.id, today, ctl=65)
    result = await coach_queries.wellness_history(
        db_session, user.id, days=7, metrics=["sleep_seconds", "readiness"], granularity="daily"
    )
    assert result["points"][-1] == {"date": str(today), "sleep_seconds": None, "readiness": None}
