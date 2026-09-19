---

description: "Task list for Freestyle Session Negotiation"
---

# Tasks: Freestyle Session Negotiation

**Input**: Design documents from `specs/011-freestyle-session-negotiation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: Included. Constitution Principle V mandates tests ship with any `app/engine/` change — this
feature modifies `app/engine/freestyle_selector.py`, so its behavior (including the cases it must NOT
override, per Principle IV) is pinned in `tests/test_engine/` and `tests/test_llm/`.

**Organization**: grouped by user story (spec.md P1/P2/P3), each independently completable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: which user story this task belongs to (US1/US2/US3)

## Path Conventions

Single project (existing monolith) — all paths relative to the repository root, per `plan.md`'s Structure
Decision. No new files are created by this feature.

---

## Phase 1: Setup

No setup tasks — this feature extends three existing modules, introduces no new dependency, file, or
configuration (per `plan.md`'s Technical Context).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: All three optional parameters (`requested_workout_type`, `max_duration_minutes`,
`template_id`) are threaded through the *same* tool call and the *same* two engine functions — there is no
way to split this plumbing by user story without editing the same function signatures three times. Every
user story phase below builds on this.

**⚠️ CRITICAL**: Complete before starting any user story phase.

- [X] T001 [P] Extend `choose_workout_type()` and `WorkoutTypeChoice` in
      `app/engine/freestyle_selector.py`: add a `requested_workout_type: str | None = None` parameter that,
      when given, becomes `chosen` directly and bypasses `avoid_workout_types` filtering entirely (Research
      Decision 3); add a `default_conflicts: bool` field on `WorkoutTypeChoice`, True whenever `chosen !=
      preferences[0]` (Research Decision 6, data-model.md)
- [X] T002 Extend `build_freestyle_suggestion()` in `app/engine/freestyle_selector.py`: accept
      `requested_workout_type` (forwarded to `choose_workout_type()`) and `requested_template_id: str |
      None = None`; when `requested_template_id` is a member of the resolved `workout_type`'s own
      candidates (`_candidates_for()`), start the rotation there instead of at `day_ordinal`'s position,
      otherwise leave today's rotation unchanged; append a conflict-note sentence to `reasoning_summary`
      when `choice.default_conflicts` is True, following the exact pattern already used for
      `preference_overridden` (depends on T001)
- [X] T003 [P] In `app/llm/tools.py`, add a module-level helper that groups
      `session_library.load_library()`'s templates by `workout_type` and renders `id — purpose — intent —
      suits` per template, called once at import time (Research Decision 2)
- [X] T004 Extend the `get_freestyle_session_suggestion` tool definition in `app/llm/tools.py`: add optional
      `requested_workout_type` (enum of `freestyle_selector.VALID_WORKOUT_TYPES`), `max_duration_minutes`
      (integer, 15–300), and `template_id` (enum + description built from T003) parameters, per
      `contracts/freestyle-suggestion-tool.md` (depends on T003)
- [X] T005 [P] In `app/llm/chat.py`, thread `args=args` into `_execute_tool`'s dispatch for
      `"get_freestyle_session_suggestion"` — today the only tool call in that dispatch not passed its own
      arguments
- [X] T006 Extend `_tool_get_freestyle_session_suggestion()` in `app/llm/chat.py` to accept `args: dict`:
      read `requested_workout_type`/`max_duration_minutes`/`template_id`; after `choose_workout_type()`
      resolves inside `build_freestyle_suggestion()`, validate a supplied `template_id` belongs to that
      resolved type's own candidates before use — a mismatch or unknown id is passed through as `None`
      (Research Decision 5); forward the three values to `build_freestyle_suggestion()` as
      `requested_workout_type`, `available_minutes` (reusing the existing parameter, Research Decision 4),
      and `requested_template_id` (depends on T002, T004, T005)

**Checkpoint**: every parameter now flows end to end from the tool call to the engine and back. Each user
story phase below adds tests pinning a specific slice of this already-complete behavior.

---

## Phase 3: User Story 1 - Ask for a specific type and get it (Priority: P1) 🎯 MVP

**Goal**: An explicit workout-type request is honored even when it conflicts with the fitness-driven
default, with a plain-language conflict note instead of a refusal; a request for an unsupported type cannot
silently become a different type.

**Independent Test**: `quickstart.md` Scenarios A, B, C.

### Tests for User Story 1

- [X] T007 [P] [US1] Add cases to `TestChooseWorkoutType` in `tests/test_engine/test_freestyle_selector.py`:
      `requested_workout_type` overrides both the TSB-derived default type and a standing
      `avoid_workout_types` entry for that same type (FR-009, US1 Acceptance Scenario 1); `default_conflicts`
      is True only when the request disagrees with what `preferences[0]` would have chosen, False when it
      already matches (US1 Acceptance Scenario 2)
- [X] T008 [P] [US1] Add a case to `tests/test_engine/test_freestyle_selector.py` (build-suggestion tests)
      verifying `reasoning_summary` carries the appended conflict-note sentence only when
      `default_conflicts` is True, and is unchanged from today's text otherwise
- [X] T009 [P] [US1] Add cases to `tests/test_llm/test_freestyle_tools.py`: `args={"requested_workout_type":
      "intervals"}` threaded through `_tool_get_freestyle_session_suggestion()` produces a suggestion with
      `workout_type == "intervals"`; assert the tool schema's `requested_workout_type` enum (T004) contains
      exactly the 4 supported types, so an unsupported value can never reach the engine as a valid argument
      (FR-008 is pinned at the schema boundary here — the athlete-facing "we don't offer that" wording is a
      conversational concern verified live via `quickstart.md` Scenario C, not by this test)

**No new production code in this phase** — T001/T002/T004/T006 (Foundational) already deliver the full
behavior; these tests pin it against the specific acceptance criteria of this story.

**Checkpoint**: `quickstart.md` Scenarios A, B, C pass.

---

## Phase 4: User Story 2 - Steer which concrete session is offered (Priority: P2)

**Goal**: A one-off style preference can select among sessions that already exist in the library for the
resolved workout type; a mismatched or unknown choice never reaches the athlete as a fabricated session.

**Independent Test**: `quickstart.md` Scenario D.

### Tests for User Story 2

- [X] T010 [P] [US2] Add cases to `tests/test_engine/test_freestyle_selector.py`:
      `requested_template_id` matching a candidate of the resolved `workout_type` is used as the rotation's
      starting point (result's `template_id` equals it, when fittable); a `requested_template_id` belonging
      to a *different* workout type, or an unknown id, is ignored and today's `day_ordinal` rotation applies
      unchanged (US2 Acceptance Scenario 2, FR-005)
- [X] T011 [P] [US2] Add a case to `tests/test_llm/test_freestyle_tools.py`: a `template_id` belonging to a
      workout type other than the one `choose_workout_type()` resolves for the request is dropped (forwarded
      as `None`) before reaching `build_freestyle_suggestion()` — pins the `chat.py`-side membership check
      (Research Decision 5)

**No new production code in this phase** — same reasoning as User Story 1's phase; Foundational already
implements the rotation override and the membership check.

**Checkpoint**: `quickstart.md` Scenario D passes.

---

## Phase 5: User Story 3 - Respect a stated time limit (Priority: P3)

**Goal**: `max_duration_minutes` bounds the returned session's duration; a ceiling too tight for anything to
fit is reported plainly, never silently exceeded.

**Independent Test**: `quickstart.md` Scenarios E, F.

### Tests for User Story 3

- [X] T012 [P] [US3] Add cases to `tests/test_engine/test_freestyle_selector.py` (build-suggestion tests):
      a supplied duration ceiling (forwarded as `available_minutes`) bounds `duration_minutes` in the
      result; `NoSuitableTemplateError` is still raised, unchanged, when no candidate of the resolved type
      can be fit within it even after `_fit_with_relaxed_tolerance()`'s three passes
- [X] T013 [P] [US3] Add a case to `tests/test_llm/test_freestyle_tools.py`: `args={"max_duration_minutes":
      N}` too tight for any session of the resolved type returns `{"available": False, "reason": ...}`
      rather than an over-length session (US3 Acceptance Scenario 2)

**No new production code in this phase** — `available_minutes`/`fit_template()` already implement
ceiling-fitting (Research Decision 4); this phase wires and pins it, nothing more.

**Checkpoint**: `quickstart.md` Scenarios E, F pass. Combined with US1/US2, Scenarios A–F all pass; Scenario
G (no preference expressed → unchanged behavior, FR-007) is covered implicitly — every pre-existing test in
both files that calls these functions with no new arguments continues to pass unmodified throughout this
feature.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T014 [P] Update `CLAUDE.md`: document the three new optional parameters on
      `get_freestyle_session_suggestion`, the rule that an explicit type request overrides a standing
      `disliked_workout_types` entry for that one suggestion only (FR-009), and the closed-template-enum
      mechanism (Research Decision 2) — per the Development Workflow rule that `CLAUDE.md` is updated in the
      same change that introduces a non-obvious business rule
- [X] T015 [P] Run `ruff check app/ tests/` and fix anything this feature introduced
- [X] T016 Run `pytest tests/` (full suite) and confirm no regression outside this feature's own new/modified
      tests
- [ ] T017 Walk through `quickstart.md` Scenarios A–G manually against a real Telegram bot in freestyle mode

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none — no tasks
- **Foundational (Phase 2)**: blocks every user story — all three read/write the same tool parameters and
  the same two engine functions
- **Polish (Phase 6)**: depends on all three user stories being complete

### User Story Dependencies

- **US1 (P1)**: depends on Foundational only. No dependency on US2 or US3.
- **US2 (P2)**: depends on Foundational only. Independent of US1 — a different slice of the same already-built
  plumbing.
- **US3 (P3)**: depends on Foundational only. Independent of US1/US2 for the same reason.

Unlike spec 010 (where US2 depended on US1's own callback handler), the three stories here are genuinely
independent of each other because Foundational already delivers all production code — each story phase only
adds tests for its own slice.

### Within Each Phase

T001 → T002 (same file, sequential edit); T003 → T004 (same file, sequential edit); T005 is independent of
T003/T004; T006 depends on T002, T004, and T005 all being in place.

### Parallel Opportunities

- T001 and T003 (different files, no shared dependency) in parallel
- T005 in parallel with T001–T004 (different file)
- T007, T008, T009 (US1 tests) in parallel once Foundational is complete
- T010, T011 (US2 tests) in parallel
- T012, T013 (US3 tests) in parallel
- Once Foundational (T001–T006) is done, all three user story test phases can proceed fully in parallel —
  they touch the same two test files but disjoint test cases, and none depends on another story's tests

---

## Parallel Example: Foundational

```bash
# Engine-side and LLM-side plumbing, together (different files):
Task: "Extend choose_workout_type() / WorkoutTypeChoice in app/engine/freestyle_selector.py"
Task: "Build the template-enum helper in app/llm/tools.py"
Task: "Thread args=args into _execute_tool's get_freestyle_session_suggestion dispatch in app/llm/chat.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Foundational → US1
2. **STOP and VALIDATE**: `quickstart.md` Scenario A — an athlete can ask for a specific type against their
   fitness signal and get it, honestly framed. This is the headline gap flagged in conversation ("don't make
   me fight the bot").
3. US2 and US3 are each a small, independent slice of the same already-built plumbing — pick either next.

### Incremental Delivery

1. Foundational → all three parameters flow end to end, unverified by story-specific tests yet
2. US1 → test independently → demo (explicit type request honored, with an honest conflict note)
3. US2 → test independently → demo (style preference steers which existing session is offered)
4. US3 → test independently → demo (duration ceiling respected, or declined plainly)

## Notes

- [P] tasks touch different files, or independent cases in the same file, and have no unmet dependency
- Commit after each task or logical group
- Verify each test fails before implementing the task that makes it pass (T001–T006 land first here, since
  the plumbing is one connected change — write T007–T013 against it, confirm each fails on the
  pre-Foundational code if reverted, then keep the Foundational implementation)
- No Alembic migration, no new file — every task edits an existing file already named in `plan.md`
