"""Repository-level tests for the dated coach journal (Enduragent parity review,
2026-09-20). Covers the deterministic surface — persistence, dedup, date-range query —
mirroring tests/test_db/test_meal_entries.py's structure. Tool-level dispatch and
validation live in tests/test_llm/test_memory_query_tool.py.
"""
from __future__ import annotations

from datetime import date

from app.db.models.coach_journal import CoachJournalEntry
from app.db.models.user import User
from app.db.repositories import journal_repo


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


# ── create() ──────────────────────────────────────────────────────────────────


async def test_create_persists_every_field(db_session):
    user = await _make_user(db_session, 9001)
    created = await journal_repo.create(
        db_session,
        user_id=user.id,
        entry_date=date(2026, 7, 12),
        category="injury",
        source="deterministic",
        text="Blessure signalée : genou (modérée), récupération estimée 14j.",
    )
    await db_session.commit()

    assert created is True
    rows = await journal_repo.query(db_session, user.id, date(2026, 7, 1), date(2026, 7, 31))
    assert len(rows) == 1
    row = rows[0]
    assert row.entry_date == date(2026, 7, 12)
    assert row.category == "injury"
    assert row.source == "deterministic"
    assert "genou" in row.text


async def test_create_truncates_overlong_text(db_session):
    user = await _make_user(db_session, 9002)
    long_text = "x" * 500
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=date.today(), category="event",
        source="llm", text=long_text,
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date.today(), date.today())
    assert len(rows[0].text) == 300


async def test_create_deduplicates_identical_entry_same_day(db_session):
    """A retried tool call / a hook firing twice must not double-write the same fact —
    on_conflict_do_nothing on (user, date, category, normalized text), same pattern as
    activity_repo.bulk_insert()."""
    user = await _make_user(db_session, 9003)
    today = date.today()

    first = await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="fatigue",
        source="llm", text="Fatigue inhabituelle signalée après le boulot.",
    )
    second = await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="fatigue",
        source="llm", text="  Fatigue INHABITUELLE signalée   après le boulot.  ",
    )
    await db_session.commit()

    assert first is True
    assert second is False  # normalized (trim/lower/collapsed spaces) matches
    rows = await journal_repo.query(db_session, user.id, today, today)
    assert len(rows) == 1


async def test_create_does_not_dedup_across_different_categories_or_dates(db_session):
    user = await _make_user(db_session, 9004)
    today = date.today()

    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="fatigue",
        source="llm", text="Même texte",
    )
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="motivation",
        source="llm", text="Même texte",
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, today, today)
    assert len(rows) == 2


async def test_cascade_delete_on_user_removal(db_session):
    from sqlalchemy import delete, select

    user = await _make_user(db_session, 9005)
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=date.today(), category="event",
        source="deterministic", text="Test cascade",
    )
    await db_session.commit()

    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()

    remaining = (
        await db_session.execute(
            select(CoachJournalEntry).where(CoachJournalEntry.user_id == user.id)
        )
    ).scalars().all()
    assert remaining == []


# ── query() ───────────────────────────────────────────────────────────────────


async def test_query_filters_by_date_range(db_session):
    user = await _make_user(db_session, 9101)
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=date(2026, 1, 1), category="event",
        source="deterministic", text="hors plage",
    )
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=date(2026, 6, 15), category="event",
        source="deterministic", text="dans la plage",
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, date(2026, 6, 1), date(2026, 6, 30))
    assert len(rows) == 1
    assert rows[0].text == "dans la plage"


async def test_query_filters_by_category(db_session):
    user = await _make_user(db_session, 9102)
    today = date.today()
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="injury",
        source="deterministic", text="injury note",
    )
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="goal_change",
        source="deterministic", text="goal note",
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, today, today, category="injury")
    assert len(rows) == 1
    assert rows[0].category == "injury"


async def test_query_filters_by_keyword_case_insensitive(db_session):
    user = await _make_user(db_session, 9103)
    today = date.today()
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="physique",
        source="llm", text="Douleur au GENOU signalée",
    )
    await journal_repo.create(
        db_session, user_id=user.id, entry_date=today, category="physique",
        source="llm", text="Rien à signaler cette semaine",
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user.id, today, today, keyword="genou")
    assert len(rows) == 1
    assert "GENOU" in rows[0].text


async def test_query_respects_limit_and_orders_most_recent_first(db_session):
    user = await _make_user(db_session, 9104)
    for i in range(5):
        await journal_repo.create(
            db_session, user_id=user.id, entry_date=date(2026, 1, 1 + i), category="event",
            source="deterministic", text=f"entry {i}",
        )
    await db_session.commit()

    rows = await journal_repo.query(
        db_session, user.id, date(2026, 1, 1), date(2026, 1, 31), limit=2
    )
    assert len(rows) == 2
    assert rows[0].entry_date == date(2026, 1, 5)  # most recent first
    assert rows[1].entry_date == date(2026, 1, 4)


async def test_query_scopes_to_the_right_user(db_session):
    user_a = await _make_user(db_session, 9105)
    user_b = await _make_user(db_session, 9106)
    today = date.today()
    await journal_repo.create(
        db_session, user_id=user_a.id, entry_date=today, category="event",
        source="deterministic", text="A's entry",
    )
    await db_session.commit()

    rows = await journal_repo.query(db_session, user_b.id, today, today)
    assert rows == []
