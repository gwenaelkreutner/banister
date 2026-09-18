"""chat_repo.token_usage_by_day() — cost visibility for the agentic chat loop (found
2026-09-18: the API returns token counts on every call but nothing kept them before this).

Run against both backends (db_session fixture, spec 003 portability): the day-grouping
uses `func.date()` on a datetime column, which returns a plain string on SQLite and a
`date` object on PostgreSQL — exactly the kind of cross-backend gotcha this suite exists
to catch (see tests/test_db/conftest.py's own docstring).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from app.db.models.chat_message import ChatMessage
from app.db.models.user import User
from app.db.repositories import chat_repo


def _dt(*args) -> datetime:
    """UtcDateTime (app/db/types.py) refuses naive datetimes — every seeded timestamp
    needs an explicit tzinfo."""
    return datetime(*args, tzinfo=UTC)


async def _seed_user(db_session) -> uuid.UUID:
    user = User(id=uuid.uuid4(), telegram_id=1, first_name="Test")
    db_session.add(user)
    await db_session.flush()
    return user.id


async def _seed_message(
    db_session, user_id, *, role="assistant", created_at, tokens_in=None, tokens_out=None
) -> None:
    db_session.add(ChatMessage(
        id=uuid.uuid4(),
        user_id=user_id,
        role=role,
        content="peu importe",
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        created_at=created_at,
    ))
    await db_session.flush()


async def test_sums_tokens_per_day_across_multiple_turns(db_session):
    user_id = await _seed_user(db_session)
    day1 = _dt(2026, 9, 1, 10, 0, 0)
    day1_later = _dt(2026, 9, 1, 18, 0, 0)
    day2 = _dt(2026, 9, 2, 9, 0, 0)

    await _seed_message(db_session, user_id, created_at=day1, tokens_in=1000, tokens_out=200)
    await _seed_message(db_session, user_id, created_at=day1_later, tokens_in=1500, tokens_out=300)
    await _seed_message(db_session, user_id, created_at=day2, tokens_in=900, tokens_out=150)

    result = await chat_repo.token_usage_by_day(
        db_session, user_id, date(2026, 9, 1), date(2026, 9, 2)
    )

    assert len(result) == 2
    assert result[0].day == date(2026, 9, 1)
    assert result[0].tokens_input == 2500
    assert result[0].tokens_output == 500
    assert result[0].turns == 2
    assert result[1].day == date(2026, 9, 2)
    assert result[1].tokens_input == 900
    assert result[1].turns == 1


async def test_a_day_with_no_turns_is_absent_not_zero(db_session):
    user_id = await _seed_user(db_session)
    await _seed_message(
        db_session, user_id, created_at=_dt(2026, 9, 1, 10, 0), tokens_in=100, tokens_out=20
    )

    result = await chat_repo.token_usage_by_day(
        db_session, user_id, date(2026, 9, 1), date(2026, 9, 5)
    )

    assert [r.day for r in result] == [date(2026, 9, 1)]  # 2..5 simply don't appear


async def test_user_role_messages_are_excluded(db_session):
    user_id = await _seed_user(db_session)
    await _seed_message(db_session, user_id, role="user", created_at=_dt(2026, 9, 1, 9, 0))
    await _seed_message(
        db_session, user_id, role="assistant", created_at=_dt(2026, 9, 1, 10, 0),
        tokens_in=500, tokens_out=100,
    )

    result = await chat_repo.token_usage_by_day(
        db_session, user_id, date(2026, 9, 1), date(2026, 9, 1)
    )

    assert len(result) == 1
    assert result[0].turns == 1  # the user-role row doesn't inflate this
    assert result[0].tokens_input == 500


async def test_pre_migration_messages_with_null_tokens_are_excluded(db_session):
    """A message created before this column existed has tokens_input/output = NULL —
    it must not show up as a phantom zero-token turn."""
    user_id = await _seed_user(db_session)
    await _seed_message(db_session, user_id, created_at=_dt(2026, 9, 1, 10, 0))  # no tokens

    result = await chat_repo.token_usage_by_day(
        db_session, user_id, date(2026, 9, 1), date(2026, 9, 1)
    )

    assert result == []


async def test_outside_the_requested_range_is_excluded(db_session):
    user_id = await _seed_user(db_session)
    await _seed_message(
        db_session, user_id, created_at=_dt(2026, 8, 1, 10, 0), tokens_in=100, tokens_out=20
    )

    result = await chat_repo.token_usage_by_day(
        db_session, user_id, date(2026, 9, 1), date(2026, 9, 30)
    )

    assert result == []
