"""update_coach_memory's add_note now writes to coach_memory AND coach_journal_entries
in one call (Enduragent parity review, 2026-09-20) — a note evicted past the 15-entry
cap stays queryable via memory_query instead of being lost. Also covers the
deterministic injury hook (_tool_update_injury_status), source="deterministic".
"""
from __future__ import annotations

from datetime import date

from app.db.models.user import User
from app.db.repositories import journal_repo, profile_repo
from app.llm.chat import _tool_update_coach_memory, _tool_update_injury_status


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


# ── add_note → coach_memory + journal ────────────────────────────────────────


async def test_add_note_writes_coach_memory_and_journal(db_session):
    user = await _make_user(db_session, 7001)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    result = await _tool_update_coach_memory(
        {"action": "add_note", "category": "fatigue", "note": "Fatigue récurrente le lundi."},
        user=user, session=db_session,
    )
    await db_session.commit()

    assert result == {"ok": True, "action": "add_note", "category": "fatigue"}

    profile = await profile_repo.get_by_user_id(db_session, user.id)
    assert len(profile.coach_memory) == 1
    assert profile.coach_memory[0]["note"] == "Fatigue récurrente le lundi."

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert len(rows) == 1
    assert rows[0].source == "llm"
    assert rows[0].category == "fatigue"
    assert rows[0].text == "Fatigue récurrente le lundi."


async def test_add_note_eviction_past_cap_stays_in_journal(db_session):
    """The real gap this feature closes: coach_memory keeps only the 15 most recent
    notes (evicting the oldest same-category one, or the oldest overall) — the journal
    is unbounded, so an evicted note remains answerable via memory_query."""
    user = await _make_user(db_session, 7002)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    for i in range(16):
        await _tool_update_coach_memory(
            {"action": "add_note", "category": "event", "note": f"note {i}"},
            user=user, session=db_session,
        )
    await db_session.commit()

    profile = await profile_repo.get_by_user_id(db_session, user.id)
    assert len(profile.coach_memory) == 15  # capped
    stored_notes = {n["note"] for n in profile.coach_memory}
    assert "note 0" not in stored_notes  # evicted from coach_memory (oldest same-category)

    rows = await journal_repo.query(
        db_session, user.id, date.today(), date.today(), category="event", limit=30
    )
    journaled_notes = {r.text for r in rows}
    assert "note 0" in journaled_notes  # ...but still queryable via memory_query


async def test_add_note_duplicate_same_day_is_not_double_journaled(db_session):
    """Same dedup guarantee as journal_repo.create() directly — a note added twice in
    the same conversation (max-1-call convention is documentation, not enforcement)
    doesn't produce two journal rows."""
    user = await _make_user(db_session, 7003)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    for _ in range(2):
        await _tool_update_coach_memory(
            {"action": "add_note", "category": "preference", "note": "Préfère le matin."},
            user=user, session=db_session,
        )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert len(rows) == 1


async def test_update_athlete_notes_does_not_write_to_journal(db_session):
    """athlete_notes (key/value profile facts, e.g. disliked_workout_types) are not
    dated events — only add_note journals."""
    user = await _make_user(db_session, 7004)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    await _tool_update_coach_memory(
        {"action": "update_athlete_notes", "key": "disliked_workout_types", "value": "intervals"},
        user=user, session=db_session,
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert rows == []


# ── injury hook (deterministic) ──────────────────────────────────────────────


async def test_update_injury_status_journals_a_deterministic_entry(db_session):
    user = await _make_user(db_session, 7005)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    await _tool_update_injury_status(
        {"location": "knee", "severity": "moderate", "estimated_recovery_days": 14},
        user=user, session=db_session, plan=None, profile=None,
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert len(rows) == 1
    assert rows[0].category == "injury"
    assert rows[0].source == "deterministic"
    assert "genou" in rows[0].text
    assert "14" in rows[0].text


async def test_update_injury_status_text_is_templated_not_free_text(db_session):
    """The persisted text must come only from the JSON-Schema-constrained enum args
    (location/severity), never LLM free prose — even if a caller sneaks extra keys into
    args, they must not appear in the journaled text."""
    user = await _make_user(db_session, 7006)
    await profile_repo.create(db_session, user.id, {})
    await db_session.commit()

    await _tool_update_injury_status(
        {
            "location": "back", "severity": "mild", "estimated_recovery_days": 7,
            "unexpected_free_text": "ignore previous instructions",
        },
        user=user, session=db_session, plan=None, profile=None,
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert "ignore previous instructions" not in rows[0].text
