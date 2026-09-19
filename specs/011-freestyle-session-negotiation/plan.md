# Implementation Plan: Freestyle Session Negotiation

**Branch**: `011-freestyle-session-negotiation` | **Date**: 2026-09-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/011-freestyle-session-negotiation/spec.md`

## Summary

`get_freestyle_session_suggestion` (spec 009) today takes no parameters — a session request in chat
ignores whatever the athlete actually said and always falls back to a pure TSB-driven type choice plus a
day-rotated template. This feature lets the LLM extract three optional signals from the athlete's message
(`requested_workout_type`, `max_duration_minutes`, `template_id`) and pass them to the same tool, while
every number in the answer (`target_tss`, duration) continues to come from `app/engine/freestyle_selector.py`
unchanged. An explicit type request overrides the fitness-driven default (never blocked), with an advisory
note when it conflicts — mirroring the existing guardrail pattern (spec 006: consultative, never
authoritative). A style preference can only select among templates that already exist in the library — the
tool schema's `template_id` is a closed enum built from `session_library.load_library()`, so an invalid or
mismatched choice is a no-op that falls back to today's rotation, never a fabricated session.

## Technical Context

**Language/Version**: Python 3.13 (unchanged)

**Primary Dependencies**: none new — extends `app/engine/freestyle_selector.py`, `app/llm/tools.py`,
`app/llm/chat.py`; reuses `app/engine/session_library.py::load_library()` and `app/engine/fitting.py`
unchanged

**Storage**: SQLite (unchanged) — no schema change; nothing introduced by this feature is persisted (FR-010)

**Testing**: pytest — `tests/test_engine/test_freestyle_selector.py` (extend), `tests/test_llm/test_freestyle_tools.py`
(extend)

**Target Platform**: unchanged

**Project Type**: single project (existing monolith) — no new top-level structure

**Performance Goals**: none beyond existing — still exactly one `get_freestyle_session_suggestion` call per
request, no additional LLM round trip or tool iteration added

**Constraints**: MUST NOT let the LLM determine or override `target_tss`/duration (Constitution Principle I
/ FR-003); MUST NOT let the LLM select a session structure outside what `session_library.load_library()`
already contains (FR-004/FR-005)

**Scale/Scope**: single-user — parameter additions to one existing tool schema, small extensions to two
existing engine functions, no new files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Check | Result |
|---|---|---|
| I. Deterministic Engine, Zero LLM Load Calculation | The LLM only supplies routing parameters (which type, which existing template, what duration ceiling) — `target_tss` and the fitted duration are still produced by `choose_workout_type()`/`fit_template()`, unchanged math | PASS |
| II. Single-User, Local-First, No Cloud Dependency | No new external integration | PASS |
| III. Clean Layered Architecture | New logic lives in `app/engine/freestyle_selector.py` (pure engine); `app/llm/tools.py` only reads `session_library.load_library()` metadata to build a closed enum, no calculation; `app/llm/chat.py` only forwards/validates parameters, same shape as every other tool handler | PASS |
| IV. Explicit Data Provenance, Never Estimate Silently | No new stored value; an invalid/unsupported request is reported as such (FR-008), never silently substituted | PASS |
| V. Engine Logic Is Test-Covered | Both changed functions live in `app/engine/freestyle_selector.py` — ships with `tests/test_engine/test_freestyle_selector.py` extensions, including the "must NOT override" cases (invalid template id, unsupported type) | PASS |

No violations requiring justification — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/011-freestyle-session-negotiation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── freestyle-suggestion-tool.md
└── tasks.md   # /speckit-tasks — not created by this command
```

### Source Code (repository root)

Single project, additive within the existing structure:

```text
app/
├── engine/
│   └── freestyle_selector.py   # MODIFIED — choose_workout_type() gains requested_workout_type
│                                #            (overrides the TSB-derived default, sets a conflict note
│                                #            when it disagrees with it); build_freestyle_suggestion()
│                                #            gains requested_workout_type + requested_template_id
│                                #            (existing available_minutes param reused for
│                                #            max_duration_minutes — no new parameter needed there)
└── llm/
    ├── tools.py                 # MODIFIED — get_freestyle_session_suggestion's parameters gain
    │                             #            requested_workout_type (enum of the 4 supported types),
    │                             #            max_duration_minutes, template_id (enum built from
    │                             #            session_library.load_library() at import time, same
    │                             #            "restart to reload" convention load_library() already has)
    └── chat.py                   # MODIFIED — _execute_tool forwards args to
                                   #            _tool_get_freestyle_session_suggestion(args=...), which
                                   #            extracts the three optional fields, validates template_id
                                   #            membership against the resolved workout_type's own
                                   #            candidates (mismatch → ignored, falls back to rotation),
                                   #            and forwards everything to build_freestyle_suggestion()

tests/
├── test_engine/
│   └── test_freestyle_selector.py   # MODIFIED — requested type override + conflict note; requested
│                                     #            template honored when valid, ignored when invalid or
│                                     #            of the wrong workout_type; no-preference path unchanged
└── test_llm/
    └── test_freestyle_tools.py      # MODIFIED — args threading from _execute_tool; unsupported
                                      #            requested type falls back cleanly; tool schema exposes
                                      #            the enum built from the current library
```

**Structure Decision**: additive within the existing layout, consistent with how specs 009/010 were
delivered — no new files, parameter extensions to two existing engine functions and one existing tool.

## Complexity Tracking

*No violations — table omitted per Constitution Check above.*
