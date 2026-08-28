"""Publication orchestration — approval lifecycle and the per-session report
(spec 005 US1 = T013; US2's approval-gate tests join this file at T020/T021).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.db.models.user import User
from app.db.repositories import plan_repo, publication_repo
from app.engine.plan_builder import generate_plan
from app.engine.schemas import TrainingPlanSchema
from app.providers.intervals.calendar import EXTERNAL_ID_PREFIX, iter_horizon_sessions
from app.services import publication
from tests.fake_intervals import FakeCalendarClient
from tests.test_engine.test_plan_builder import make_profile


async def _make_user(session) -> User:
    user = User(telegram_id=42, first_name="Test")
    session.add(user)
    await session.flush()
    return user


async def _make_plan(session, user_id, *, legacy_first: bool = False):
    schema = generate_plan(make_profile())
    if legacy_first:
        schema.weeks[0].sessions[0].steps = None
    start = schema.weeks[0].start_date
    technical = schema.model_dump(mode="json")
    plan = await plan_repo.create(
        session, user_id, plan_technical=technical, start_date=start, end_date=start
    )
    return plan


async def test_request_publication_creates_a_pending_approval(db_session):
    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)

    request = await publication.request_publication(db_session, user, plan)

    assert request.approval.status == "pending"
    assert request.approval.content_hash
    assert request.session_count > 0
    assert request.approval.horizon_start <= request.approval.horizon_end
    # First publication: the device-forwarding caveat must be present (FR-012, SC-009).
    assert "transfert vers ton appareil" in request.text


async def test_session_without_steps_is_flagged_and_not_counted(db_session):
    """A legacy (no-steps) session, forced into the horizon, is shown in the request but
    excluded from the publishable count and never written (FR-009)."""
    from datetime import timedelta


    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id, legacy_first=True)
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    hs, he = publication.compute_horizon(schema, plan.start_date)

    horizon = iter_horizon_sessions(schema, hs, he)
    legacy_in_horizon = [p for p in horizon if p.spec.steps is None]
    if not legacy_in_horizon:
        # The forced-legacy session landed outside the 2-week horizon — widen it.
        he = he + timedelta(days=90)
        horizon = iter_horizon_sessions(schema, hs, he)
        legacy_in_horizon = [p for p in horizon if p.spec.steps is None]

    text, publishable = publication.build_approval_request_text(
        schema, hs, he, is_first_publication=True
    )
    assert legacy_in_horizon
    assert "ne sera pas publiée" in text
    assert publishable == sum(1 for p in horizon if p.spec.steps is not None)


async def test_execute_publication_writes_one_entry_per_created_session(db_session):
    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await publication.request_publication(db_session, user, plan)
    await publication_repo.mark_approved(db_session, request.approval.id)

    client = FakeCalendarClient()
    report = await publication.execute_publication(
        db_session, client, user, plan, request.approval
    )

    assert report.created == client.create_calls > 0
    assert report.failed == 0
    entries = await publication_repo.get_active_entries_for_plan(db_session, user.id, plan.id)
    assert len(entries) == report.created
    # Every entry carries the approval that authorised it (FR-005).
    assert all(e.approval_id == request.approval.id for e in entries)
    # Report is per-session, never a blanket "done" (FR-006).
    assert report.text.count("✅") >= report.created


# ── US2: nothing is written without a matching, approved consent ──────────────


async def _approved_request(db_session, user, plan):
    request = await publication.request_publication(db_session, user, plan)
    await publication_repo.mark_approved(db_session, request.approval.id)
    return request


async def test_no_approval_at_all_refuses(db_session):

    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)

    with pytest.raises(publication.PublicationNotAuthorized):
        await publication.authorize_publication(db_session, uuid.uuid4(), plan)


async def test_pending_approval_is_not_a_green_light(db_session):

    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await publication.request_publication(db_session, user, plan)  # stays pending

    client = FakeCalendarClient()
    with pytest.raises(publication.PublicationNotAuthorized):
        await publication.execute_publication(db_session, client, user, plan, request.approval)
    assert client.create_calls == 0  # nothing written (FR-001)


async def test_plan_change_after_approval_refuses_and_writes_nothing(db_session):

    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await _approved_request(db_session, user, plan)

    # Modify an in-horizon session's name through the plan (FR-004 scenario).
    schema = TrainingPlanSchema.model_validate(plan.plan_technical)
    hs, he = request.approval.horizon_start, request.approval.horizon_end

    target = iter_horizon_sessions(schema, hs, he)[0]
    for w in schema.weeks:
        for s in w.sessions:
            if s is target.spec:
                s.description_fr = s.description_fr + " (modifiée)"
    plan.plan_technical = schema.model_dump(mode="json")
    flag_modified(plan, "plan_technical")
    await db_session.flush()

    client = FakeCalendarClient()
    with pytest.raises(publication.StaleApprovalError):
        await publication.execute_publication(db_session, client, user, plan, request.approval)
    assert client.create_calls == 0
    entries = await publication_repo.get_active_entries_for_plan(db_session, user.id, plan.id)
    assert entries == []


# ── US3: republication converges instead of accumulating ─────────────────────


async def test_five_executions_of_one_approval_stay_at_one_entry_per_session(db_session):
    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await _approved_request(db_session, user, plan)
    client = FakeCalendarClient(
        seed=[{"id": 9, "external_id": "cycling-coach:x:y", "name": "foreign"}]
    )

    first = await publication.execute_publication(db_session, client, user, plan, request.approval)
    for _ in range(4):
        rep = await publication.execute_publication(
            db_session, client, user, plan, request.approval
        )
        assert rep.created == 0 and rep.updated == 0
        assert rep.unchanged == first.created

    ours = client.by_prefix(EXTERNAL_ID_PREFIX)
    assert len(ours) == first.created
    assert client.create_calls == first.created  # created once, never again
    entries = await publication_repo.get_active_entries_for_plan(db_session, user.id, plan.id)
    assert len(entries) == first.created
    assert client.find("cycling-coach:x:y") is not None  # foreign entry untouched


async def test_interrupted_publication_retried_reaches_the_same_state(db_session):
    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await _approved_request(db_session, user, plan)

    # First run: fail after the 2nd create, simulating a mid-publication crash.
    client = FakeCalendarClient()
    real_create = client.create_event
    calls = {"n": 0}

    async def _crash_after_two(payload):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("connection dropped")
        return await real_create(payload)

    client.create_event = _crash_after_two
    partial = await publication.execute_publication(
        db_session, client, user, plan, request.approval
    )
    assert partial.created == 2 and partial.failed >= 1

    # Retry against the same calendar + same approval — no crash this time.
    client.create_event = real_create
    resumed = await publication.execute_publication(
        db_session, client, user, plan, request.approval
    )

    assert resumed.unchanged == 2  # the two already written are recognised
    assert resumed.failed == 0
    ours = client.by_prefix(EXTERNAL_ID_PREFIX)
    externals = [e["external_id"] for e in ours]
    assert len(externals) == len(set(externals))  # no duplicates from the retry
    entries = await publication_repo.get_active_entries_for_plan(db_session, user.id, plan.id)
    assert len(entries) == len(ours)


async def test_declining_writes_nothing_and_records_the_no(db_session):
    user = await _make_user(db_session)
    plan = await _make_plan(db_session, user.id)
    request = await publication.request_publication(db_session, user, plan)

    await publication_repo.mark_declined(db_session, request.approval.id)

    refreshed = await publication_repo.get_approval(db_session, request.approval.id)
    assert refreshed.status == "declined"  # kept, not deleted (FR-003)
    entries = await publication_repo.get_active_entries_for_plan(db_session, user.id, plan.id)
    assert entries == []
