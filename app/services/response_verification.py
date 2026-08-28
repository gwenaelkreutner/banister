"""Verify that what a coaching response says about the athlete's numbers is true
(spec 006 US3, FR-017–FR-021).

A `MetricRegistry` records exactly which metric values were put in front of the model
while the context was built — the definition of "retrieved". After generation, the
response is scanned for `(metric term, number)` pairs and each is checked against the
registry: a mismatch or a value stated for an unretrieved metric is a failure, the
claim is withheld (not the whole response — spec 006 research R6), and the failure is
recorded so its frequency is measurable (FR-021).

Deterministic, no LLM call (FR-022, SC-010): a non-deterministic checker cannot satisfy
reproducibility, and FR-022 forbids the model performing evaluation. Verification is
**keyword-anchored** rather than "extract every number" — a real coaching reply carries
mostly durations and zone codes, and an unanchored checker would raise a dozen false
alarms to catch two real claims (spec 006 research R5).

This lives in `services/` rather than `app/llm/` on purpose: it must run around
generation while staying pure, and Principle III forbids `llm/` performing evaluation.
The chat orchestrator calls it.

Populated in US3 (T030-T035).
"""
from __future__ import annotations
