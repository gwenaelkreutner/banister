"""Calendar publication against the events API (spec 005 US1 = T012).

publish_sessions() is create-only here — idempotent diffing is US3 (T030). These tests
pin the event payload shape (contracts/calendar-publication.md §2) and the FR-009 refusal
of a session with no steps.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.engine.plan_builder import generate_plan
from app.providers.intervals.calendar import (
    EXTERNAL_ID_PREFIX,
    build_event_payload,
    build_external_id,
    publish_sessions,
)
from tests.test_engine.test_plan_builder import make_profile

_WIDE = (date(2000, 1, 1), date(2100, 1, 1))


class _StubClient:
    def __init__(self, *, fail_on: str | None = None):
        self.payloads: list[dict] = []
        self._fail_on = fail_on

    async def create_event(self, payload: dict) -> dict:
        if self._fail_on and self._fail_on in payload["name"]:
            raise RuntimeError("calendar said no")
        self.payloads.append(payload)
        return {"id": 900000 + len(self.payloads)}


def test_build_external_id_shape():
    plan_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    ext = build_external_id(plan_id, date(2026, 9, 2), "intervals", 3, 1)
    assert ext == "banister:12345678:2026-09-02:intervals-3-1"
    assert ext.startswith(EXTERNAL_ID_PREFIX)


def test_build_event_payload_fields():
    payload = build_event_payload(
        date(2026, 9, 2),
        "Intervalles 3×12min Z4",
        "banister:abc:2026-09-02:intervals-3-1",
        "Warmup\n- 15m 50-65%",
    )
    assert payload == {
        "start_date_local": "2026-09-02T00:00:00",
        "category": "WORKOUT",
        "type": "Ride",
        "name": "Intervalles 3×12min Z4",
        "external_id": "banister:abc:2026-09-02:intervals-3-1",
        "description": "Warmup\n- 15m 50-65%",
    }
    # Never sent — intervals.icu derives these from the DSL (R3/R4).
    for derived in ("workout_doc", "moving_time", "icu_training_load", "id", "uid"):
        assert derived not in payload


async def test_publish_sessions_writes_one_workout_event_per_structured_session():
    schema = generate_plan(make_profile())
    plan_id = uuid.uuid4()
    client = _StubClient()

    outcomes = await publish_sessions(client, schema, plan_id, *_WIDE)

    created = [o for o in outcomes if o.status == "created"]
    assert created and len(created) == len(client.payloads)
    assert all(p["category"] == "WORKOUT" for p in client.payloads)
    assert all(p["external_id"].startswith(EXTERNAL_ID_PREFIX) for p in client.payloads)
    assert all(o.intervals_event_id for o in created)


async def test_session_without_steps_is_refused_not_written():
    schema = generate_plan(make_profile())
    # Simulate a legacy session (spec 004 keeps these loadable).
    schema.weeks[0].sessions[0].steps = None
    client = _StubClient()

    outcomes = await publish_sessions(client, schema, uuid.uuid4(), *_WIDE)

    refused = [o for o in outcomes if o.status == "refused"]
    assert len(refused) == 1
    assert refused[0].external_id not in {p["external_id"] for p in client.payloads}


async def test_one_failing_session_does_not_abandon_the_rest():
    schema = generate_plan(make_profile())
    first_name = None
    for w in schema.weeks:
        for s in w.sessions:
            if s.steps is not None:
                first_name = s.description_fr
                break
        if first_name:
            break
    client = _StubClient(fail_on=first_name)

    outcomes = await publish_sessions(client, schema, uuid.uuid4(), *_WIDE)

    assert any(o.status == "failed" for o in outcomes)
    assert any(o.status == "created" for o in outcomes)  # FR-028
