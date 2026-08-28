"""`/publish` — approve a horizon of sessions and write them to the athlete's
intervals.icu calendar (spec 005 US1 = T017-T019, US2 = T023, US4 = T041).

Registered before `chat_router` in app/bot/setup.py (catch-all ordering). Populated in
Phase 3 — this feature is the project's first outbound mutation, and every callback here
is a write path gated on a recorded, content-bound approval (app/services/publication.py).
"""
from __future__ import annotations

from aiogram import Router

publish_router = Router(name="publish")
