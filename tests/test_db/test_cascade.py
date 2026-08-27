"""Foreign-key cascade enforcement tests (spec 003 FR-006, research R1).

test_cascade_fails_without_the_pragma is the falsification test: it proves the pragma in
app/db/client.py is load-bearing by disabling it and confirming the delete then leaves an
orphan. Without this test, test_cascade_delete_removes_dependents could pass for a reason
unrelated to the pragma — SQLAlchemy's ORM-level cascade, for instance — and a regression
that silently disabled the pragma would go undetected.

Both tests delete via a Core `delete()` statement rather than `session.delete(user)`.
Using the ORM form surfaced a pre-existing, unrelated issue: `User.training_plans` has no
`cascade="all, delete-orphan"`, so the ORM's unit-of-work tries to null out
`training_plans.user_id` before deleting the user — which fails, since that column is
`NOT NULL`. This reproduces identically on PostgreSQL, so it predates this port and is not
a portability bug; `session.delete(user)` is not used anywhere in the application, so it
is latent rather than a live regression. Left unfixed here as out of scope for a
storage-engine port — worth a follow-up if account deletion is ever built.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select

from app.db.models.session_log import SessionLog
from app.db.models.training_plan import TrainingPlan
from app.db.models.user import User


async def _make_user_and_plan(session, telegram_id: int):
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    plan = TrainingPlan(
        user_id=user.id,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 11, 24),
        plan_technical={"weeks": []},
    )
    session.add(plan)
    await session.flush()
    return user, plan


async def test_cascade_delete_removes_dependents(db_session):
    """Deletes via a Core statement rather than session.delete(user): FR-006 and the
    pragma govern what the DATABASE does on delete, independent of the ORM's own
    relationship-level cascade bookkeeping — the same guarantee also has to hold for a
    delete issued directly in SQL, from another tool, or from code that never loaded the
    relationships into a session. (session.delete(user) hits an unrelated pre-existing
    issue on this schema — see the module docstring below the last test — and is not
    used anywhere in the application, so it is deliberately not exercised here.)"""
    user, plan = await _make_user_and_plan(db_session, 200)
    log = SessionLog(
        user_id=user.id,
        plan_id=plan.id,
        week_number=1,
        day_of_week=0,
        logged_date=date(2026, 8, 24),
        status="done",
        source="intervals_icu",
    )
    db_session.add(log)
    await db_session.commit()

    from app.db.models.user import User as UserModel

    await db_session.execute(delete(UserModel).where(UserModel.id == user.id))
    await db_session.commit()

    remaining_plans = (await db_session.execute(
        select(TrainingPlan).where(TrainingPlan.user_id == user.id)
    )).scalars().all()
    remaining_logs = (await db_session.execute(
        select(SessionLog).where(SessionLog.user_id == user.id)
    )).scalars().all()
    assert remaining_plans == []
    assert remaining_logs == []


async def test_cascade_fails_without_the_pragma(sqlite_session_no_fk):
    """Falsification test (spec 003 research R1): with the pragma disabled, the exact
    same delete leaves an orphan and reports no error — this is what the pragma in
    app/db/client.py exists to prevent."""
    from app.db.models.user import User as UserModel

    user, plan = await _make_user_and_plan(sqlite_session_no_fk, 201)

    await sqlite_session_no_fk.execute(delete(UserModel).where(UserModel.id == user.id))
    await sqlite_session_no_fk.commit()  # succeeds with no error, orphan or not

    orphaned_plans = (await sqlite_session_no_fk.execute(
        select(TrainingPlan).where(TrainingPlan.user_id == user.id)
    )).scalars().all()
    assert orphaned_plans != [], (
        "expected an orphan here without the pragma — if this now passes, either "
        "SQLAlchemy's ORM cascade is compensating (masking what this test checks) or "
        "SQLite's default foreign_keys behaviour changed; either way, re-verify that "
        "test_cascade_delete_removes_dependents is actually testing the pragma"
    )
