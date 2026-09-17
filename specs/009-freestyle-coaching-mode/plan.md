# Implementation Plan: Freestyle Coaching Mode

**Branch**: `009-freestyle-coaching-mode` | **Date**: 2026-09-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-freestyle-coaching-mode/spec.md`

## Summary

Today the coach is only useful with an active, goal-driven training plan. This feature adds a second
coaching mode — freestyle — where the athlete has no target goal or plan: the coach still reacts to every
published activity and still surfaces training-load/recovery guardrails, but session guidance becomes
on-demand ("propose me a session now") based on current fitness and declared preferences instead of a
periodization phase. The athlete switches between freestyle and goal mode via the existing `/goal` command,
which gains a fifth option instead of a new command. The technical approach (Phase 0 research) found that
most of the plan-agnostic plumbing already exists (`chat.py`, `guardrail_service.py`) — the real work is:
relaxing a NOT NULL schema constraint that currently causes freestyle-mode activities to be silently
dropped, a new deterministic session-selection function that substitutes fitness state for periodization
phase, and one new LLM tool wired the same way the existing eight are.

## Technical Context

**Language/Version**: Python 3.13 (unchanged)

**Primary Dependencies**: aiogram v3, FastAPI, SQLAlchemy + `aiosqlite`, existing LLM provider abstraction
(`app/llm/providers/`) — no new dependency introduced

**Storage**: SQLite (unchanged) — one new Alembic migration relaxing `session_logs.plan_id` /
`.week_number` / `.day_of_week` to nullable

**Testing**: pytest (`tests/test_engine/`, `tests/test_services/`, `tests/test_bot/`, `tests/test_providers/`
as applicable), `ruff check app/ tests/`

**Target Platform**: unchanged — single self-hosted process (Telegram bot + FastAPI webhook)

**Project Type**: single project (existing monolith under `app/`) — no new top-level structure

**Performance Goals**: no new performance target; freestyle session suggestion is a single synchronous
deterministic computation (grid search already used by `fit_template()`, bounded and cheap per its own
docstring) inside the existing chat request/response cycle

**Constraints**: MUST NOT let the LLM compute or restate session figures itself (Constitution Principle I);
MUST NOT silently guess a session when fitness history is insufficient (Principle IV, FR-011)

**Scale/Scope**: single-user (Constitution Principle II) — no concurrency, no multi-tenant concern; scope is
one new engine module, one schema migration, one new LLM tool, and an extension to an existing command

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Check | Result |
|---|---|---|
| I. Deterministic Engine, Zero LLM Load Calculation | Freestyle session figures (workout type, duration, target TSS, zone) are produced by a new pure function in `app/engine/` plus the existing `fitting.fit_template()`; the LLM only narrates the tool's result and is subject to the existing response-verification pass (spec 006 US3) for any numbers it restates | PASS |
| II. Single-User, Local-First, No Cloud Dependency | No new external integration; reuses the existing intervals.icu connection and LLM provider; coaching mode is derived from existing single-athlete state, not a multi-tenant concept | PASS |
| III. Clean Layered Architecture | New selector lives in `app/engine/` (no aiogram/LLM import); the new LLM tool in `app/llm/tools.py` only reads the selector's output, matching how existing tools already work; `app/bot/routers/goal.py` stays the only place with FSM/keyboard logic for the switch | PASS |
| IV. Explicit Data Provenance, Never Estimate Silently | Freestyle suggestions read the same authoritative fitness figures (`get_current_fitness()`) everything else uses; insufficient history produces an explicit "not enough data" result (FR-011), never a guessed session; mode-switch history preservation (FR-012) keeps provenance of past data intact | PASS |
| V. Engine Logic Is Test-Covered | New `app/engine/` selector ships with tests under `tests/test_engine/`, including the case where history is insufficient (mirrors how `guardrails.py`/`baselines.py` test their "must not fire" cases) | PASS (commitment, enforced at implementation) |

No violations requiring justification — Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/009-freestyle-coaching-mode/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   ├── llm-tool-session-suggestion.md
│   └── mode-switch-interaction.md
└── tasks.md              # Phase 2 output (/speckit-tasks — not created by this command)
```

### Source Code (repository root)

Single project — existing monolith, no new top-level directory. Changed/added files:

```text
app/
├── engine/
│   └── freestyle_selector.py        # NEW — (workout_type, target_tss) from fitness state + recent load,
│                                     #        replaces periodization phase as the selection input (Research
│                                     #        Decision 4); calls session_library.load_library() filtered by
│                                     #        workout_type only, and fitting.fit_template() unchanged
├── services/
│   └── activity_feedback.py         # MODIFIED — replace the "no_plan" early return with a real freestyle
│                                     #            path: build a SessionLog with plan_id=NULL, skip plan
│                                     #            matching, keep fitness feedback + highlight selection
│                                     #            (Research Decision 3); Outcome gains "freestyle", "no_plan"
│                                     #            retired
├── llm/
│   ├── tools.py                     # MODIFIED — add get_freestyle_session_suggestion to TOOL_DEFINITIONS
│   └── chat.py                      # MODIFIED — dispatch the new tool in _execute_tool; filter
│                                     #            TOOL_DEFINITIONS by coaching_mode(user) before the call
│                                     #            at chat.py:211 (Research Decision 6)
├── bot/
│   └── routers/
│       └── goal.py                  # MODIFIED — remove the `plan is None` block at line 44; add a fifth
│                                     #            keyboard option + goal:type:freestyle branch that
│                                     #            deactivates the plan and withdraws future calendar entries
│                                     #            (contracts/mode-switch-interaction.md)
└── db/
    └── models/
        └── session_log.py           # MODIFIED — plan_id, week_number, day_of_week become nullable=True

migrations/versions/
└── <new>_freestyle_nullable_session_log.py   # NEW — Alembic autogenerate for the column relaxation above

tests/
├── test_engine/
│   └── test_freestyle_selector.py   # NEW
├── test_services/
│   └── test_activity_feedback.py    # MODIFIED — cover the new "freestyle" outcome path
└── test_bot/
    └── test_goal.py                 # MODIFIED — cover the new fifth keyboard option and the removed guard
```

**Structure Decision**: no change to the project's overall layout (`app/{bot,db,engine,llm,providers,
services}`, single SQLite file, single process). This feature is additive within that existing structure —
one new engine module, targeted modifications to three existing modules, one schema migration — consistent
with how specs 004-008 were each delivered without restructuring the codebase.
