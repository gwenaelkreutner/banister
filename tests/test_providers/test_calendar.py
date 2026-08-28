"""Calendar publication against the events API (spec 005 US1 = T012, US3 = T027-T029).

The API does not upsert (research R2), so publish_sessions() must read the window and
diff on external_id. These tests pin the payload shape (contract §2), the FR-009 refusal
of a no-steps session, and the US3 idempotence / foreign-entry guarantees.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.engine.plan_builder import generate_plan
from app.providers.intervals.calendar import (
    EXTERNAL_ID_PREFIX,
    KnownEntry,
    build_event_payload,
    build_external_id,
    iter_horizon_sessions,
    publish_sessions,
)
from app.providers.intervals.workout_dsl import hash_session_content, render_dsl
from tests.fake_intervals import FakeCalendarClient
from tests.test_engine.test_plan_builder import make_profile

_WIDE = (date(2000, 1, 1), date(2100, 1, 1))
_FOREIGN = {
    "id": 42,
    "external_id": "cycling-coach:2026-08-27:sweet-spot-2x15",
    "category": "WORKOUT",
    "name": "Sweet spot 2x15",
}


def _known_for(schema, plan_id, client) -> dict[str, KnownEntry]:
    """Reconstruct what execute_publication would pass as known_entries from what the
    fake calendar currently holds under our prefix."""
    known: dict[str, KnownEntry] = {}
    for planned in iter_horizon_sessions(schema, *_WIDE):
        spec = planned.spec
        if spec.steps is None:
            continue
        ext = build_external_id(
            plan_id,
            planned.session_date,
            spec.workout_type,
            planned.week_number,
            planned.day_of_week,
        )
        remote = client.find(ext)
        if remote is None:
            continue
        name = spec.description_fr or spec.workout_type
        rendered = render_dsl(spec.steps, schema.zones)
        known[ext] = KnownEntry(
            intervals_event_id=str(remote["id"]),
            content_hash=hash_session_content(planned.session_date, name, rendered),
        )
    return known


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
    for derived in ("workout_doc", "moving_time", "icu_training_load", "id", "uid"):
        assert derived not in payload


async def test_publish_sessions_writes_one_workout_event_per_structured_session():
    schema = generate_plan(make_profile())
    client = FakeCalendarClient()

    outcomes = await publish_sessions(client, schema, uuid.uuid4(), *_WIDE)

    created = [o for o in outcomes if o.status == "created"]
    ours = client.by_prefix(EXTERNAL_ID_PREFIX)
    assert created and len(created) == len(ours)
    assert all(e["category"] == "WORKOUT" for e in ours)
    assert all(o.intervals_event_id for o in created)


async def test_session_without_steps_is_refused_not_written():
    schema = generate_plan(make_profile())
    schema.weeks[0].sessions[0].steps = None
    client = FakeCalendarClient()

    outcomes = await publish_sessions(client, schema, uuid.uuid4(), *_WIDE)

    refused = [o for o in outcomes if o.status == "refused"]
    assert len(refused) == 1
    assert client.find(refused[0].external_id) is None


async def test_five_consecutive_publications_produce_one_entry_per_session():
    """SC-003 — the naive create-only implementation demonstrably fails this (R2)."""
    schema = generate_plan(make_profile())
    plan_id = uuid.uuid4()
    client = FakeCalendarClient(seed=[dict(_FOREIGN)])

    for i in range(5):
        known = _known_for(schema, plan_id, client)
        outcomes = await publish_sessions(client, schema, plan_id, *_WIDE, known_entries=known)
        if i == 0:
            assert all(o.status == "created" for o in outcomes if o.status != "refused")
        else:
            assert all(o.status == "unchanged" for o in outcomes if o.status != "refused")

    ours = client.by_prefix(EXTERNAL_ID_PREFIX)
    externals = [e["external_id"] for e in ours]
    assert len(externals) == len(set(externals))  # exactly one per session
    assert client.create_calls == len(set(externals))  # created once, never again


async def test_foreign_entry_survives_every_republication_untouched():
    schema = generate_plan(make_profile())
    plan_id = uuid.uuid4()
    client = FakeCalendarClient(seed=[dict(_FOREIGN)])

    for _ in range(3):
        known = _known_for(schema, plan_id, client)
        await publish_sessions(client, schema, plan_id, *_WIDE, known_entries=known)

    assert client.find("cycling-coach:2026-08-27:sweet-spot-2x15") == _FOREIGN
    assert client.update_calls == 0 and client.delete_calls == 0


async def test_a_changed_session_is_updated_not_duplicated():
    schema = generate_plan(make_profile())
    plan_id = uuid.uuid4()
    client = FakeCalendarClient()
    await publish_sessions(client, schema, plan_id, *_WIDE)
    before = len(client.by_prefix(EXTERNAL_ID_PREFIX))
    known = _known_for(schema, plan_id, client)  # snapshot BEFORE the change (as the DB would hold)

    # Rename the first in-horizon structured session.
    for w in schema.weeks:
        renamed = False
        for s in w.sessions:
            if s.steps is not None:
                s.description_fr = (s.description_fr or "") + " (v2)"
                renamed = True
                break
        if renamed:
            break

    outcomes = await publish_sessions(client, schema, plan_id, *_WIDE, known_entries=known)

    assert any(o.status == "updated" for o in outcomes)
    assert len(client.by_prefix(EXTERNAL_ID_PREFIX)) == before  # no new events


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

    client = FakeCalendarClient()
    original_create = client.create_event

    async def _flaky(payload):
        if first_name and first_name in payload["name"]:
            raise RuntimeError("calendar said no")
        return await original_create(payload)

    client.create_event = _flaky

    outcomes = await publish_sessions(client, schema, uuid.uuid4(), *_WIDE)

    assert any(o.status == "failed" for o in outcomes)
    assert any(o.status == "created" for o in outcomes)  # FR-028
