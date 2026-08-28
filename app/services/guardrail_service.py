"""Guardrail orchestration (spec 006): assemble the athlete's data, run the pure
evaluators in `app/engine/guardrails.py`, honour declined acknowledgements, and hand
`GuardrailFinding`s to the narration layer.

Provider-agnostic and aiogram-free (Constitution Principle III). This layer reads
`wellness` / `session_logs` / `activities`, calls the engine, and returns findings; it
performs **no** writes — a guardrail advises, it never mutates a plan or a calendar
(FR-023, SC-006). An accepted recommendation is dispatched to the *existing* approval
paths (spec 004 `plan_modifier`, spec 005 `authorize_publication`), which is what makes
SC-006 structural rather than tested.

Populated per user story: workload assembly (US1 = T018), recovery assembly (US2 = T025),
decline filtering (US4 = T042), insufficiency reasons (US5 = T048).
"""
from __future__ import annotations
