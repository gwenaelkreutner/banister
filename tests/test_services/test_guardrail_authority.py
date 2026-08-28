"""Guardrails advise; they do not seize control (spec 006 US4, FR-023–FR-026, SC-006).
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models.guardrail import GuardrailAcknowledgement
from app.db.models.publication import PublicationApproval, PublishedEntry
from app.db.models.user import User
from app.db.repositories import guardrail_repo, wellness_repo
from app.services import guardrail_service
from app.services.guardrail_service import (
    assemble_recovery_findings,
    assemble_workload_findings,
)

TODAY = date(2026, 8, 28)


async def _user(session) -> User:
    u = User(telegram_id=1, first_name="Test")
    session.add(u)
    await session.flush()
    return u


async def _seed_overload(session, user_id):
    await wellness_repo.upsert(session, user_id, TODAY, ctl=44.0, atl=64.0, ramp_rate=6.5)


# ── FR-023 / SC-006: zero writes from a firing guardrail ─────────────────────


async def test_assembling_findings_writes_nothing(db_session):
    u = await _user(db_session)
    await _seed_overload(db_session, u.id)
    for i in range(20):  # a full RHR baseline too
        await wellness_repo.upsert(
            db_session, u.id, TODAY - timedelta(days=2 + i), resting_hr=50
        )
    await wellness_repo.upsert(
        db_session, u.id, TODAY, ctl=44.0, atl=64.0, ramp_rate=6.5, resting_hr=58
    )
    await db_session.commit()

    workload = await assemble_workload_findings(db_session, u.id, today=TODAY)
    recovery = await assemble_recovery_findings(db_session, u.id, today=TODAY)
    assert workload  # something fired

    # Nothing was written to any authority-bearing table.
    assert await db_session.scalar(select(func.count()).select_from(PublicationApproval)) == 0
    assert await db_session.scalar(select(func.count()).select_from(PublishedEntry)) == 0
    assert await db_session.scalar(select(func.count()).select_from(GuardrailAcknowledgement)) == 0
    _ = recovery


# ── FR-024 / SC-006: no write verb of its own ───────────────────────────────


def test_guardrail_service_imports_no_write_verb():
    """An accepted recommendation must flow through the existing approval paths
    (spec 004 plan_modifier, spec 005 authorize_publication) — guardrail_service must
    not carry a write path of its own. Checks the parsed AST, not the docstring prose
    (quickstart Scenario 4: "a grep, not a vibe")."""
    import ast

    tree = ast.parse(inspect.getsource(guardrail_service))
    tree.body = [n for n in tree.body if not isinstance(n, ast.Expr)]  # drop module docstring

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)

    forbidden_imports = {"plan_modifier", "flag_modified"}
    forbidden_calls = {
        "create_event", "update_event", "delete_event",
        "authorize_publication", "publish_sessions", "withdraw_event",
        "flag_modified", "add", "commit", "delete",
        "record_acknowledgement", "record_check_failure",
    }
    assert not (imported & forbidden_imports), imported & forbidden_imports
    assert not (called & forbidden_calls), called & forbidden_calls


# ── FR-025 / FR-026: a decline sticks without silencing the signal ───────────


async def test_declined_occurrence_is_demoted_not_removed(db_session):
    u = await _user(db_session)
    await _seed_overload(db_session, u.id)
    await db_session.commit()

    first = await assemble_workload_findings(db_session, u.id, today=TODAY)
    acwr = next(f for f in first if f.kind == "acwr_high")
    assert "réduis" in acwr.action.lower()  # a real recommendation

    await guardrail_repo.record_acknowledgement(
        db_session,
        user_id=u.id,
        finding_kind="acwr_high",
        occurrence_key=acwr.occurrence_key,
        decision="declined",
    )

    second = await assemble_workload_findings(db_session, u.id, today=TODAY)
    acwr2 = next(f for f in second if f.kind == "acwr_high")
    assert acwr2.observed == acwr.observed          # signal still stated (FR-026)
    assert "réduis" not in acwr2.action.lower()      # recommendation demoted (FR-025)
    assert "choisi de ne pas ajuster" in acwr2.action


async def test_decline_does_not_carry_across_a_day_boundary(db_session):
    u = await _user(db_session)
    await _seed_overload(db_session, u.id)
    # A wellness row for tomorrow too, so tomorrow's evaluation has data.
    await wellness_repo.upsert(
        db_session, u.id, TODAY + timedelta(days=1), ctl=44.0, atl=64.0, ramp_rate=6.5
    )
    await db_session.commit()

    today_findings = await assemble_workload_findings(db_session, u.id, today=TODAY)
    acwr = next(f for f in today_findings if f.kind == "acwr_high")
    await guardrail_repo.record_acknowledgement(
        db_session, user_id=u.id, finding_kind="acwr_high",
        occurrence_key=acwr.occurrence_key, decision="declined",
    )

    tomorrow_findings = await assemble_workload_findings(
        db_session, u.id, today=TODAY + timedelta(days=1)
    )
    acwr_tomorrow = next(f for f in tomorrow_findings if f.kind == "acwr_high")
    assert acwr_tomorrow.occurrence_key != acwr.occurrence_key
    assert "réduis" in acwr_tomorrow.action.lower()  # a fresh occurrence, full recommendation


async def test_accepted_occurrence_is_dropped(db_session):
    u = await _user(db_session)
    await _seed_overload(db_session, u.id)
    await db_session.commit()

    first = await assemble_workload_findings(db_session, u.id, today=TODAY)
    acwr = next(f for f in first if f.kind == "acwr_high")
    await guardrail_repo.record_acknowledgement(
        db_session, user_id=u.id, finding_kind="acwr_high",
        occurrence_key=acwr.occurrence_key, decision="accepted",
    )

    second = await assemble_workload_findings(db_session, u.id, today=TODAY)
    assert not any(f.kind == "acwr_high" for f in second)  # acted on, not re-raised
