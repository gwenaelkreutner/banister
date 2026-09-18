# Implementation Plan: Publish a Freestyle Session

**Branch**: `010-publish-freestyle-session` | **Date**: 2026-09-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-publish-freestyle-session/spec.md`

## Summary

Freestyle mode (spec 009) can suggest a session in chat, but nothing lets the athlete actually load it onto
a device — it was never written anywhere. This feature closes that gap: once the athlete confirms a
suggestion with a button (Clarification Q1: button, not a new LLM tool), it is written to their
intervals.icu calendar as a single structured workout, reusing spec 005's DSL rendering and calendar-write
primitives directly rather than its plan-horizon batch machinery, which does not fit a single ad-hoc
session. Phase 0 research found the confirmation mechanism (button → FSM data → callback) can reuse the
existing `pending_proposal` plumbing chat.py already has for plan-modification proposals, with one
important divergence: it must not block ordinary chat the way that existing flow does, since negotiating a
different session (US2) has to stay possible mid-conversation.

## Technical Context

**Language/Version**: Python 3.13 (unchanged)

**Primary Dependencies**: aiogram v3 (inline keyboard + callback query), existing `IntervalsClient`
(`app/providers/intervals/client.py`) — no new dependency

**Storage**: SQLite (unchanged) — one new table, `freestyle_published_entries` (new Alembic migration, a
plain `CREATE TABLE`, no column alteration involved)

**Testing**: pytest — `tests/test_providers/` (calendar write), `tests/test_services/` (repo/withdrawal),
`tests/test_bot/` (button/callback flow)

**Target Platform**: unchanged

**Project Type**: single project (existing monolith) — no new top-level structure

**Performance Goals**: none beyond existing — one additional calendar API call per confirmed publish,
negligible next to spec 005's documented quota headroom

**Constraints**: MUST NOT let the LLM alter session content between suggestion and publication (Constitution
Principle I / spec FR-009); MUST NOT touch any calendar entry this system did not create (Principle III/
FR-006, enforced by the `banister:` prefix + a dedicated table, not by runtime filtering alone)

**Scale/Scope**: single-user — one new table, one new small service module, one new callback handler, small
extensions to two existing files (`chat.py` tagging, `goal.py` withdrawal-on-switch)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Check | Result |
|---|---|---|
| I. Deterministic Engine, Zero LLM Load Calculation | The published session's content is exactly the `FreestyleSuggestion` spec 009's engine already computed — the button carries that content by id, the LLM is never consulted again between suggestion and write | PASS |
| II. Single-User, Local-First, No Cloud Dependency | No new external integration — reuses the existing intervals.icu connection | PASS |
| III. Clean Layered Architecture | New calendar I/O in `app/providers/intervals/calendar.py` (no DB access, matching its existing constraint); new persistence in a new repository, mirroring `publication_repo.py`; bot-layer button/callback logic stays in `app/bot/routers/chat.py` | PASS |
| IV. Explicit Data Provenance, Never Estimate Silently | A freestyle publication is recorded as exactly that — a new table shaped for it, not a plan/approval-shaped table with fields pretending a plan or a batch approval exists (research.md Decision 4) | PASS |
| V. Engine Logic Is Test-Covered | No new `app/engine/` logic — this feature is I/O (calendar write) and bot-layer plumbing (button/callback) around spec 009's existing engine output; tests target those layers instead | PASS (N/A for engine specifically — nothing new added there) |

No violations requiring justification — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/010-publish-freestyle-session/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── confirmation-button.md
│   └── single-session-write.md
└── tasks.md   # /speckit-tasks — not created by this command
```

### Source Code (repository root)

Single project, additive within the existing structure:

```text
app/
├── providers/
│   └── intervals/
│       └── calendar.py              # MODIFIED — add build_freestyle_external_id() alongside the
│                                     #            existing build_external_id(); no change to
│                                     #            publish_sessions()/withdraw_event()
├── services/
│   └── publication.py               # MODIFIED — add publish_freestyle_session(),
│                                     #            withdraw_freestyle_publications(); existing
│                                     #            plan-horizon functions untouched
├── llm/
│   ├── tools.py                     # MODIFIED — get_freestyle_session_suggestion's result gains
│                                     #            "type" and "id" (contracts/confirmation-button.md)
│   └── chat.py                      # MODIFIED — pending_proposal detection includes the freestyle
│                                     #            tool (guarded on available=true); id generation
│                                     #            in _tool_get_freestyle_session_suggestion
├── bot/
│   └── routers/
│       ├── chat.py                  # MODIFIED — attach the confirm button without changing FSM
│                                     #            state (Research Decision 2); new callback handler
│                                     #            for freestyle:publish:<id>
│       └── goal.py                  # MODIFIED — _regenerate() (freestyle → goal direction) also
│                                     #            calls withdraw_freestyle_publications() (FR-011)
└── db/
    ├── models/
    │   └── publication.py           # MODIFIED — add FreestylePublishedEntry model (new class,
    │                                 #            existing PublicationApproval/PublishedEntry
    │                                 #            untouched) — or a new file, task-level choice
    └── repositories/
        └── freestyle_publication_repo.py   # NEW — mirrors publication_repo.py's shape

migrations/versions/
└── <new>_freestyle_published_entries.py    # NEW — plain CREATE TABLE, no batch_alter_table needed

tests/
├── test_providers/
│   └── test_calendar.py             # MODIFIED — cover build_freestyle_external_id()
├── test_services/
│   └── test_publication.py          # MODIFIED — cover publish_freestyle_session(),
│                                     #            withdraw_freestyle_publications()
└── test_bot/
    └── test_chat_freestyle_publish.py   # NEW — button attach, stale-id rejection, double-tap,
                                          #        FSM state stays ACTIVE/None throughout
```

**Structure Decision**: additive within the existing layout, consistent with how spec 009 was delivered —
one new table/repository, small extensions to five existing files, no restructuring.
