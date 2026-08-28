"""Pure, deterministic evaluation of training-guardrail signals (spec 006 FR-022,
SC-010).

Every function here is a pure function of its arguments: no DB, no clock, no LLM, no
randomness. `app/services/guardrail_service.py` assembles the athlete's data and calls
these; the LLM narrates the `GuardrailFinding`s it is given and produces none itself
(Constitution Principle I, FR-022).

Populated per user story: workload evaluators (US1 = T014-T017), recovery evaluators
(US2 = T023-T024), the outlier / persistence guard (US5 = T047).
"""
from __future__ import annotations
