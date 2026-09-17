---

description: "Task list for Freestyle Coaching Mode"
---

# Tasks: Freestyle Coaching Mode

**Input**: Design documents from `specs/009-freestyle-coaching-mode/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: Included, not optional here — Constitution Principle V makes engine-logic tests non-negotiable
("Any change to `app/engine/`... MUST ship with corresponding tests... including the cases where a
threshold must NOT fire"), and `plan.md`'s Constitution Check commits to it for this feature's new selector.
Test tasks also cover the two other modified layers (services, bot) so each user story's acceptance
scenarios are pinned by an automated test, not only by the manual `quickstart.md` walkthrough.

**Organization**: grouped by user story (spec.md P1/P2/P3), each independently completable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: which user story this task belongs to (US1/US2/US3)

## Path Conventions

Single project (existing monolith) — all paths are relative to the repository root (`app/`, `tests/`,
`migrations/`), per `plan.md`'s Structure Decision. No new top-level directory.

---

## Phase 1: Setup

**Purpose**: Minimal — this is a brownfield feature with no new dependency, no new lint/build config.

- [X] T001 Create `app/engine/freestyle_selector.py` with module docstring only (no logic yet), stating its
      role per `research.md` Decision 4: replaces periodization `phase` with fitness state as the input to
      session selection, feeding into the existing `app/engine/fitting.py::fit_template()`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one piece of shared state every user story reads — "which mode is the athlete in"
(`research.md` Decision 1). Both US1 (tool filtering) and US2 (switch logic) depend on it.

**⚠️ CRITICAL**: Complete before starting US1 or US2. (US3 does not depend on this phase — see its own
section — but implement in ID order regardless for a single-developer flow.)

- [X] T002 Implement `get_coaching_mode(session, user_id) -> Literal["goal", "freestyle"]` in
      `app/services/coaching_mode.py`, backed by `plan_repo.get_active_plan(session, user_id)` returning
      `"goal"` if not `None` else `"freestyle"` — no new column, no new state (`data-model.md`)
- [X] T003 [P] Unit tests for `get_coaching_mode()` in `tests/test_services/test_coaching_mode.py`: active
      plan → `"goal"`; no active plan → `"freestyle"`; mode flips immediately after
      `plan_repo.deactivate_all_for_user()` without needing a fresh DB session

**Checkpoint**: Foundation ready — US1 and US2 implementation can begin.

---

## Phase 3: User Story 1 - Ask for a session with no goal in place (Priority: P1) 🎯 MVP

**Goal**: An athlete with no active plan asks for a session in chat and gets one concrete, fitness-appropriate
proposal — type, duration, target zone — without any periodization phase involved.

**Independent Test**: With no active plan and existing fitness history, ask the coach for a session in chat
and receive one concrete session (`quickstart.md` Scenario A).

### Tests for User Story 1

> Write these first; they must fail before the corresponding implementation task.

- [X] T004 [P] [US1] Test `app/engine/freestyle_selector.py`'s selection rule in
      `tests/test_engine/test_freestyle_selector.py`: given fitness state + recent load, returns a
      `(workout_type, target_tss)` pair with a `reasoning_summary`; given insufficient history (mirrors the
      bar `recovery_insufficiency()` already uses), returns an explicit "insufficient data" result instead
      of a guessed session (FR-011); never returns anything referencing a periodization phase or week number
      (SC-005); a hard-effort day before the request yields a lighter suggestion than a fresh-legs day
      (US1 Acceptance Scenario 3)
- [X] T005 [P] [US1] Test the `get_freestyle_session_suggestion` tool end-to-end (selector → `fit_template()`
      → tool result shape) in `tests/test_llm/test_freestyle_tools.py`, per
      `contracts/llm-tool-session-suggestion.md`'s two result shapes (`available: true/false`)
- [X] T006 [P] [US1] Test tool-list filtering in `tests/test_llm/test_freestyle_tools.py`:
      `get_freestyle_session_suggestion` is present in the tools passed to the LLM call when
      `get_coaching_mode()` returns `"freestyle"` and absent when `"goal"`; `get_upcoming_sessions`,
      `propose_plan_modification`, `propose_session_adjustment` are absent in `"freestyle"` (`research.md`
      Decision 6)

### Implementation for User Story 1

- [X] T007 [US1] Implement the selection rule in `app/engine/freestyle_selector.py`: reads current fitness
      (`app/services/fitness.py::get_current_fitness()`) and recent load
      (`app/engine/weekly_snapshot.py::compute_weekly_snapshot()`), applies athlete preferences from
      `AthleteProfile.coach_memory`/`.athlete_notes` when more than one workout type would otherwise fit
      (US1 Acceptance Scenario 2), and calls `app/engine/session_library.py::load_library()` filtered by
      `workout_type` only (no `phase`) plus `app/engine/fitting.py::fit_template()` to produce the concrete
      session (`data-model.md`'s `Session Suggestion` shape) — depends on T004 existing as a failing test
- [X] T008 [US1] Add the `get_freestyle_session_suggestion` tool definition to `TOOL_DEFINITIONS` in
      `app/llm/tools.py`, exactly as specified in `contracts/llm-tool-session-suggestion.md` (no parameters)
- [X] T009 [US1] Dispatch the new tool in `_execute_tool` in `app/llm/chat.py`: call T007's selector, wrap
      its result in the two JSON shapes from the contract, never let the LLM alter the numeric fields
- [X] T010 [US1] Filter the tool list passed at `app/llm/chat.py:211` (`tools=TOOL_DEFINITIONS`) by
      `get_coaching_mode(session, user.id)` before the call: exclude `get_freestyle_session_suggestion` in
      `"goal"` mode; exclude `get_upcoming_sessions`, `propose_plan_modification`,
      `propose_session_adjustment` in `"freestyle"` mode (depends on T002, T008)
- [X] T011 [US1] Register `target_tss`, `duration_minutes`, and `zone_code` from the tool's result into the
      chat turn's metric registry (`app/services/response_verification.py` / the registry assembled in
      `app/llm/chat.py`) so a misstated number in the narrated answer is caught the same way a misstated
      CTL/ATL figure already is (spec 006 US3, per `contracts/llm-tool-session-suggestion.md`)

**Checkpoint**: User Story 1 is fully functional and independently testable — `quickstart.md` Scenario A
(and the insufficient-history edge case, Scenario F) should pass.

---

## Phase 4: User Story 2 - Switch between freestyle and goal mode (Priority: P2)

**Goal**: The athlete switches from freestyle to goal mode (providing a goal + date) or from goal to
freestyle mode (dropping the plan) via `/goal`, in both directions, without losing history.

**Independent Test**: From freestyle mode, set a goal via `/goal` and confirm a plan is generated; from goal
mode, choose "mode libre" via `/goal` and confirm the plan is deactivated and history is intact
(`quickstart.md` Scenarios C and D).

### Tests for User Story 2

- [X] T012 [P] [US2] Test in `tests/test_bot/test_goal_change.py` (extend): `/goal` no longer replies
      "Aucun plan actif — lance /setup." when there is no active plan; instead shows the (now five-option)
      keyboard
- [X] T013 [P] [US2] Test the `goal:type:freestyle` callback in `tests/test_bot/test_goal_change.py`
      (extend): deactivates the active plan, calls the calendar-withdrawal path for that plan's future
      entries, and replies with a summary naming what changed (calendar withdrawn) and what's kept
      (activities, session logs, coach memory counts unchanged — SC-004, SC-006)
- [X] T014 [P] [US2] Test that choosing `goal:type:freestyle` with no active plan already (already in
      freestyle mode) replies idempotently ("Déjà en mode libre.") rather than erroring, in
      `tests/test_bot/test_goal_change.py`
- [X] T015 [P] [US2] Test that `_regenerate()` omits the "ce qui change" old-plan diff paragraph when
      entering goal mode from freestyle (no `old_plan` to diff against), while still showing "ce qui est
      gardé", in `tests/test_bot/test_goal_change.py`

### Implementation for User Story 2

- [X] T016 [US2] Remove the `if plan is None: ... "Aucun plan actif"` block in `cmd_goal()` at
      `app/bot/routers/goal.py:44` (depends on T012 existing as a failing test)
- [X] T017 [US2] Add the fifth keyboard option "🚴 Pas d'objectif / mode libre" (`goal:type:freestyle`) to
      `_goal_kb()` in `app/bot/routers/goal.py`, per `contracts/mode-switch-interaction.md`
- [X] T018 [US2] Implement the `goal:type:freestyle` callback handler in `app/bot/routers/goal.py`:
      `plan_repo.deactivate_all_for_user()` when a plan is active, then the existing calendar-withdrawal
      capability behind `/unpublish` scoped to that plan's future entries, then the summary reply (depends
      on T016, T017)
- [X] T019 [US2] Make the same handler idempotent: if there was no active plan, skip straight to the
      "Déjà en mode libre." reply (depends on T018)
- [X] T020 [US2] Update `_regenerate()` in `app/bot/routers/goal.py` to special-case `old_plan is None`
      (entry from freestyle mode): omit the diff paragraph, keep the "ce qui est gardé" paragraph (depends
      on T016)

**Checkpoint**: User Stories 1 AND 2 both work independently — `quickstart.md` Scenarios C and D pass, and
combined with US1, an athlete can switch to freestyle and immediately get a suggestion.

---

## Phase 5: User Story 3 - Post-activity feedback keeps working without a plan (Priority: P3)

**Goal**: An athlete in freestyle mode who publishes an activity to intervals.icu still gets post-activity
feedback, without any claim of a matched or missed planned session; training-load/recovery guardrails keep
firing exactly as in goal mode.

**Independent Test**: While in freestyle mode, publish an activity and confirm feedback arrives and a
`session_logs` row exists with `plan_id IS NULL` (`quickstart.md` Scenario B); confirm a guardrail signal
still fires under a triggering pattern (`quickstart.md` Scenario E).

### Tests for User Story 3

- [X] T021 [P] [US3] Test in `tests/test_db/test_migrations.py` (extend): after migration, inserting a
      `session_logs` row with `plan_id=None`, `week_number=None`, `day_of_week=None` succeeds
- [X] T022 [P] [US3] Test the new `"freestyle"` outcome in `tests/test_services/test_activity_feedback.py`
      (extend): `assemble_activity_feedback()` with no active plan creates a `SessionLog` with
      `plan_id=None`, never calls `evaluate_activity_plan_match`, still returns fitness feedback and a
      highlight, and the returned `outcome` is `"freestyle"` (not the retired `"no_plan"`)
- [X] T023 [P] [US3] Regression test in `tests/test_services/test_guardrail_service.py` (extend): pin that
      `assemble_workload_findings`/`assemble_recovery_findings` produce identical findings for a given
      history whether or not an active plan exists (`research.md` Decision 7 — must keep passing unchanged)

### Implementation for User Story 3

- [X] T024 [US3] Relax `plan_id`, `week_number`, `day_of_week` to `nullable=True` in
      `app/db/models/session_log.py` (depends on T021 existing as a failing test)
- [X] T025 [US3] Generate the Alembic migration for T024 (`alembic revision --autogenerate`) under
      `migrations/versions/` — never a hand-written SQL file, per the project's Development Workflow
      (depends on T024)
- [X] T026 [US3] Add `"freestyle"` to `ActivityFeedbackContext.Outcome` and retire `"no_plan"` in
      `app/services/activity_feedback.py` (depends on T024, T025)
- [X] T027 [US3] Replace the `no_plan` early return in `assemble_activity_feedback()` with a real freestyle
      path: create the `SessionLog` with `plan_id=None`/`week_number=None`/`day_of_week=None`, skip
      `evaluate_activity_plan_match` entirely, still call `_build_fitness_feedback()` and
      `select_highlight()`, set `outcome="freestyle"`, in `app/services/activity_feedback.py` (depends on
      T026; must make T022 pass)
- [X] T028 [US3] Update `notify_detected_activity()` in `app/providers/intervals/notifier.py`: replace the
      silent skip on `outcome == "no_plan"` with real delivery for `outcome == "freestyle"`, and update the
      notification copy path so it never states a planned session was matched or missed for this outcome
      (depends on T027)

**Checkpoint**: All three user stories are independently functional — `quickstart.md` Scenarios A–F all
pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T029 [P] Update `CLAUDE.md` (repository root): add the coaching-mode concept (derived, not stored),
      `app/engine/freestyle_selector.py` to the architecture map and navigation table, and the `/goal`
      fifth-option switch — per the Development Workflow rule that `CLAUDE.md` is updated in the same
      change that introduces a new module or non-obvious business rule
- [X] T030 [P] Run `ruff check app/ tests/` and fix anything this feature introduced
- [X] T031 Run `pytest tests/` (full suite) and confirm no regression outside this feature's own new/modified
      tests
- [ ] T032 Walk through `quickstart.md` Scenarios A–F manually against a real Telegram bot and a connected
      intervals.icu test account, including Scenario F (brand-new account, insufficient history)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup; **blocks US1 and US2** (both call `get_coaching_mode()`)
- **US3 (Phase 5)**: does **not** depend on Phase 2 — it never calls `get_coaching_mode()` directly, only on
  `plan is None`, which every touched code path already checks directly. It can be implemented in parallel
  with US1/US2 by a second developer, or done last in a solo, priority-ordered flow (P1 → P2 → P3) as listed
  here
- **Polish (Phase 6)**: depends on all three user stories being complete

### User Story Dependencies

- **US1 (P1)**: depends on Foundational (T002). No dependency on US2 or US3.
- **US2 (P2)**: depends on Foundational (T002) and reuses the calendar-withdrawal capability from spec 005
  (already shipped, no new dependency). No dependency on US1 or US3, though it is more meaningful to a user
  once US1 exists (freestyle mode has something to do).
- **US3 (P3)**: no dependency on Foundational, US1, or US2 — it only touches the ingestion/notification path.
  Independently testable on its own (`quickstart.md` Scenario B does not require US1's chat tool).

### Within Each User Story

Tests before implementation; model/schema changes before the service logic that depends on them (US3:
T024 → T025 → T026 → T027); story complete before moving to the next priority in a solo flow.

### Parallel Opportunities

- T003 (Foundational test) can run alongside T001 (Setup) once T002 exists to test against
- T004, T005, T006 (US1 tests, different files) in parallel
- T012, T013, T014, T015 (US2 tests, same file but independent cases) can be drafted in parallel then
  merged
- T021, T022, T023 (US3 tests, three different files) in parallel
- US1, US2, and US3 implementation phases can run in parallel across three developers once Phase 2 is done,
  since (per the Dependencies section above) US3 has no runtime dependency on Foundational, US1, or US2

---

## Parallel Example: User Story 1

```bash
# Tests, together:
Task: "Test freestyle_selector's fitness→session rule in tests/test_engine/test_freestyle_selector.py"
Task: "Test get_freestyle_session_suggestion tool result shapes in tests/test_llm/test_freestyle_tools.py"
Task: "Test tool-list filtering by coaching mode in tests/test_llm/test_freestyle_tools.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1)
2. **STOP and VALIDATE**: `quickstart.md` Scenario A (and F) — an athlete with no plan can already get a
   session suggestion in chat. This alone is the headline gap from the original request ("aujourd'hui mon
   application est utilisable que en mode il y a un plan").
3. Demo if ready — US2 (switching) and US3 (post-activity feedback) are real gaps but are additive on top of
   a working US1.

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → test independently → MVP demo (an athlete can already use freestyle mode meaningfully in chat, even
   before the `/goal` switch exists — e.g. tested via an account that never ran `/goal`)
3. US2 → test independently → demo (switching now works both ways)
4. US3 → test independently → demo (published activities now get feedback in freestyle mode too)

### Solo-Developer Suggested Order

Given the Dependencies section above, the priority order (P1 → P2 → P3) is also a valid, fully sequential
solo path — nothing forces reordering for a single implementer.

## Notes

- [P] tasks touch different files (or independent cases in the same test file) and have no unmet dependency
- Commit after each task or logical group, per the project's own commit convention
- Verify each test fails before implementing the task that makes it pass
- Avoid combining T024/T025 (model change and its migration) into one commit-worthy step done out of
  order — the model change must exist before `alembic revision --autogenerate` has anything to diff against
