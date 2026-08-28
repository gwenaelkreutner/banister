"""Publish / withdraw structured sessions against the intervals.icu events API, scoped
to our own `banister:` prefix (spec 005 US1 = T014-T015, US3 = T030-T031, US4 = T039).

All I/O and ordering live here; the pure DSL text transform is in workout_dsl.py. Every
read-for-diff, update and delete filters on `external_id.startswith("banister:")`, which
is what makes FR-015 ("never modify entries it did not create") true by construction
rather than by care. Populated in Phase 3.
"""
from __future__ import annotations

EXTERNAL_ID_PREFIX = "banister:"
