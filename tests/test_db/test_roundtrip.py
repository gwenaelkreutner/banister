"""Document round-trip and in-place mutation tests (spec 003 FR-007, FR-008, FR-011)."""
from __future__ import annotations

import uuid
from datetime import date

from app.db.models.profile import AthleteProfile
from app.db.models.session_log import SessionLog
from app.db.models.training_plan import TrainingPlan
from app.db.models.user import User


async def _make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def test_document_roundtrips_with_nesting_and_empty_values(db_session):
    user = await _make_user(db_session, 1)
    doc = {
        "n": None,
        "empty_obj": {},
        "empty_list": [],
        "nested": {"k": [1, 2, {"z": False, "s": "text with é and 中文"}]},
        "number": 3.14159,
    }
    profile = AthleteProfile(user_id=user.id, profile=doc)
    db_session.add(profile)
    await db_session.commit()
    db_session.expunge_all()

    reread = await db_session.get(AthleteProfile, profile.id)
    assert reread.profile == doc


async def test_unknown_stays_distinguishable_from_zero_false_and_empty(db_session):
    """The highest-value assertion in this file: session_logs is dominated by nullable
    floats feeding chronic load. A metric that was never measured must not silently become
    a value that means "measured as zero"."""
    user = await _make_user(db_session, 2)
    plan = TrainingPlan(
        user_id=user.id,
        start_date=date(2026, 8, 24),
        end_date=date(2026, 11, 24),
        plan_technical={"weeks": []},
    )
    db_session.add(plan)
    await db_session.flush()

    unmeasured = SessionLog(
        user_id=user.id,
        plan_id=plan.id,
        week_number=1,
        day_of_week=0,
        logged_date=date(2026, 8, 24),
        status="done",
        source="strava",
        cardiac_drift_index=None,  # genuinely not measured
    )
    measured_zero = SessionLog(
        user_id=user.id,
        plan_id=plan.id,
        week_number=1,
        day_of_week=1,
        logged_date=date(2026, 8, 25),
        status="done",
        source="strava",
        cardiac_drift_index=0.0,  # genuinely measured as exactly zero
    )
    db_session.add_all([unmeasured, measured_zero])
    await db_session.commit()
    db_session.expunge_all()

    reread_unmeasured = await db_session.get(SessionLog, unmeasured.id)
    reread_zero = await db_session.get(SessionLog, measured_zero.id)

    assert reread_unmeasured.cardiac_drift_index is None
    assert reread_zero.cardiac_drift_index == 0.0
    assert reread_unmeasured.cardiac_drift_index is not reread_zero.cardiac_drift_index


async def test_empty_document_stays_distinguishable_from_absent(db_session):
    """AthleteProfile.athlete_notes defaults to {} — an athlete with no notes yet must not
    be indistinguishable from a row where the column itself failed to populate."""
    user = await _make_user(db_session, 3)
    profile = AthleteProfile(user_id=user.id, profile={"level": "intermediate"})
    db_session.add(profile)
    await db_session.commit()
    db_session.expunge_all()

    reread = await db_session.get(AthleteProfile, profile.id)
    assert reread.athlete_notes == {}
    assert reread.athlete_notes is not None
    assert reread.coach_memory == []


async def test_document_mutated_in_place_is_persisted(db_session):
    """The codebase already documents this hazard for the current backend: mutating a
    JSON-mapped column without flag_modified() is silently discarded. This test pins the
    correct (flagged) usage as a regression guard for the port, not as a demonstration of
    the bug — see contracts/persistence.md guarantee 3."""
    from sqlalchemy.orm.attributes import flag_modified

    user = await _make_user(db_session, 4)
    profile = AthleteProfile(user_id=user.id, profile={"level": "beginner"})
    db_session.add(profile)
    await db_session.commit()

    profile.profile["level"] = "advanced"
    flag_modified(profile, "profile")
    await db_session.commit()
    db_session.expunge_all()

    reread = await db_session.get(AthleteProfile, profile.id)
    assert reread.profile["level"] == "advanced"


async def test_identifiers_remain_valid_and_unique(db_session):
    user = await _make_user(db_session, 5)
    assert isinstance(user.id, uuid.UUID)

    reread = await db_session.get(User, user.id)
    assert reread is not None
    assert reread.id == user.id
