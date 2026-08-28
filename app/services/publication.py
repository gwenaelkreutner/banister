"""Calendar-publication orchestration (spec 005): approval lifecycle, plan<->calendar
diffing, divergence detection.

Provider-agnostic by design (plan.md Constitution Check III): the workout DSL and the
events API live in app/providers/intervals/; this module owns consent and record-keeping
and never imports aiogram. app/engine/ is not touched at all — this feature reads
sessions, it does not generate or modify them.

Only `hash_content` is implemented in Phase 2 (Foundational); the approval and diff verbs
land with their user stories (US1 = T016, US2 = T022/T025, US4 = T037).
"""
from __future__ import annotations

import hashlib
from datetime import date


def hash_content(session_date: date, name: str, rendered_dsl: str) -> str:
    """SHA-256( session_date | name | rendered_DSL_text ) — data-model.md §Content hashing.

    The rendered DSL is what actually reaches the calendar and what the athlete is shown
    in the approval request, so hashing it binds the approval to precisely the content it
    was shown for (FR-004) and lets a published entry be compared against the current
    plan to detect drift (FR-020) with no ambiguity about which fields count.

    Deliberately excluded: intervals_event_id (server-assigned, not content), approval_id
    (provenance), load/duration (derived by intervals.icu from the DSL itself — R4 — so
    including them would double-count the same information).
    """
    payload = f"{session_date.isoformat()}|{name}|{rendered_dsl}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
