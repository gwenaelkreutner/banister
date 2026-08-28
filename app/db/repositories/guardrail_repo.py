"""Persistence for training guardrails (spec 006 data-model.md §Persisted).

Only two things survive a restart, and both are records of something that *happened*
rather than something that *is*: a response check that failed (FR-021, so failure
frequency is measurable for SC-001/SC-002) and a guardrail recommendation the athlete
decided on (FR-025, so the same occurrence is not re-raised). Findings and baselines are
derived on demand, never stored — a stored finding goes stale the moment the day it
describes passes.

Populated per user story: check-failure recording (US3 = T034), acknowledgements
(US4 = T041).
"""
from __future__ import annotations
