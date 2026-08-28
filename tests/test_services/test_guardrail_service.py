"""Workload-finding assembly (spec 006 US1 = T018).

Ties the pure evaluators to the athlete's stored data. The no-writes / authority
guarantees (FR-023, SC-006) are exercised in Phase 6's test_guardrail_authority.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.db.models.user import User
from app.db.repositories import wellness_repo
from app.services.guardrail_service import (
    assemble_workload_findings,
    has_load_reduction_finding,
)

TODAY = date(2026, 8, 28)


async def _user(session) -> User:
    u = User(telegram_id=1, first_name="Test")
    session.add(u)
    await session.flush()
    return u


async def test_no_wellness_no_findings(db_session):
    u = await _user(db_session)
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []


async def test_high_ratio_and_ramp_produce_findings_most_severe_first(db_session):
    u = await _user(db_session)
    # The athlete's real 2026-08-25 shape: ATL/CTL ≈ 1.44, ramp 6.43.
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=44.0, atl=63.5, ramp_rate=6.43
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)

    kinds = [f.kind for f in findings]
    assert "acwr_high" in kinds
    assert "ramp_rate_caution" in kinds  # 6.43 is in the 5–7 caution band
    assert findings == sorted(findings, key=lambda f: f.severity, reverse=True)
    assert has_load_reduction_finding(findings)  # acwr_high requires load down
    assert all(f.action for f in findings)  # SC-003


async def test_normal_load_produces_no_manufactured_warning(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=50.0, atl=52.0, ramp_rate=2.0
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []  # FR-005


async def test_uses_latest_wellness_row_not_a_stale_one(db_session):
    u = await _user(db_session)
    await wellness_repo.upsert(
        db_session, u.id, TODAY - timedelta(days=10), ctl=40.0, atl=80.0, ramp_rate=9.0
    )
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=50.0, atl=50.0, ramp_rate=1.0
    )
    findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert findings == []  # today's row is calm; the 10-day-old spike is not used
