"""A stand-in for IntervalsClient's calendar surface (spec 005).

Models the one behaviour research R2 proved and no mock library would have surfaced:
the API does *not* upsert — `create_event` always makes a new event, even for an
`external_id` that already exists. Idempotence is the client code's job, so the fake
must not paper over it.
"""
from __future__ import annotations

from typing import Any


class FakeCalendarClient:
    def __init__(self, *, seed: list[dict] | None = None):
        self._events: list[dict] = list(seed or [])
        self._next_id = 1000 + len(self._events)
        self.create_calls = 0
        self.update_calls = 0
        self.delete_calls = 0

    async def list_events(self, *, oldest: str, newest: str) -> list[dict]:
        return [dict(e) for e in self._events]

    async def create_event(self, payload: dict) -> dict:
        self.create_calls += 1
        self._next_id += 1
        event = {"id": self._next_id, **payload}
        self._events.append(event)  # no dedupe on external_id — R2
        return dict(event)

    async def update_event(self, event_id: str, payload: dict) -> dict:
        self.update_calls += 1
        for e in self._events:
            if str(e["id"]) == str(event_id):
                e.update(payload)
                return dict(e)
        raise KeyError(f"no event {event_id}")

    async def delete_event(self, event_id: str) -> None:
        self.delete_calls += 1
        self._events = [e for e in self._events if str(e["id"]) != str(event_id)]

    # test helpers ----------------------------------------------------------
    def events(self) -> list[dict]:
        return [dict(e) for e in self._events]

    def by_prefix(self, prefix: str) -> list[dict]:
        return [e for e in self._events if str(e.get("external_id") or "").startswith(prefix)]

    def find(self, external_id: str) -> dict | Any:
        return next((e for e in self._events if e.get("external_id") == external_id), None)
