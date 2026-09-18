---

description: "Task list for Publish a Freestyle Session"
---

# Tasks: Publish a Freestyle Session

**Input**: Design documents from `specs/010-publish-freestyle-session/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: Included. No new `app/engine/` logic here (Constitution Principle V doesn't force it the way it
did for spec 009), but this feature writes to a real external calendar and handles money-can't-buy-back
consent correctly (FR-002/FR-004/FR-005/FR-010) — the kind of logic this project always pins with tests
(see spec 005/009's own test suites for the same class of guarantee).

**Organization**: grouped by user story (spec.md P1/P2/P3), each independently completable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: which user story this task belongs to (US1/US2/US3)

## Path Conventions

Single project (existing monolith) — all paths relative to the repository root, per `plan.md`'s Structure
Decision.

---

## Phase 1: Setup

- [X] T001 Create `app/db/repositories/freestyle_publication_repo.py` with a module docstring only (mirrors
      `app/db/repositories/publication_repo.py`'s shape, per `research.md` Decision 4)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The suggestion-identity and button-attachment mechanism every user story depends on — US1
publishes by id, US2's whole point is that an id becomes stale, US3 withdraws rows this phase's model
creates.

**⚠️ CRITICAL**: Complete before starting any user story phase.

- [X] T002 [P] Add the `FreestylePublishedEntry` SQLAlchemy model in `app/db/models/publication.py`
      (columns per `data-model.md`: `id`, `user_id`, `external_id` unique-per-user, `intervals_event_id`,
      `session_date`, `workout_type`, `content_hash`, `published_at`, `withdrawn_at` nullable) — no
      `plan_id`/`approval_id`/`week_number`/`day_of_week` (`research.md` Decision 4)
- [X] T003 Generate the Alembic migration for T002 (`alembic revision --autogenerate`) under
      `migrations/versions/` — a plain `CREATE TABLE`, no `batch_alter_table` needed (depends on T002)
- [X] T004 [P] Implement `app/db/repositories/freestyle_publication_repo.py`: `create(session, user_id,
      external_id, intervals_event_id, session_date, workout_type, content_hash, published_at)`,
      `get_active_for_user(session, user_id)` (not-yet-withdrawn rows), `mark_withdrawn(session, entry_id)`
      (depends on T002, T003)
- [X] T005 [P] Test `freestyle_publication_repo.py` in `tests/test_db/test_freestyle_publication_repo.py`:
      create + fetch round-trip, `get_active_for_user` excludes withdrawn rows, `mark_withdrawn` sets
      `withdrawn_at`
- [X] T006 Tag `_tool_get_freestyle_session_suggestion`'s result with `"type": "freestyle_publish"` and a
      fresh `"id"` (`uuid.uuid4().hex[:8]`) whenever `available: true`, in `app/llm/chat.py`
      (`contracts/confirmation-button.md`)
- [X] T007 Extend the `pending_proposal` detection in `app/llm/chat.py`'s chat function: the existing tuple
      (`"propose_plan_modification"`, `"propose_session_adjustment"`) gains
      `"get_freestyle_session_suggestion"`, guarded by `last_tool_result.get("available")` (depends on T006)
- [X] T008 In `app/bot/routers/chat.py::handle_chat_message`, when `pending_proposal.get("type") ==
      "freestyle_publish"`: store `pending_freestyle_id`/`pending_freestyle_suggestion` in FSM **data**
      without calling `state.set_state(...)` (`research.md` Decision 2 — state stays `ACTIVE`/`None`), and
      attach a `InlineKeyboardButton(text="📅 Publier sur intervals.icu",
      callback_data=f"freestyle:publish:{id}")` to the sent message (depends on T007)
- [X] T009 [P] Test in `tests/test_bot/test_chat_freestyle_publish.py`: a freestyle suggestion's message
      carries the confirm button; FSM state is still `ACTIVE`/`None` immediately after (never
      `PENDING_MODIFICATION`) — pins `research.md` Decision 2 (depends on T008)

**Checkpoint**: Foundation ready — every user story phase can begin.

---

## Phase 3: User Story 1 - Publish a session you're happy with (Priority: P1) 🎯 MVP

**Goal**: Confirming a proposed freestyle session via its button writes it to the athlete's intervals.icu
calendar as a single structured workout.

**Independent Test**: In freestyle mode, ask for a session, tap its confirm button, and check the
athlete's calendar for a matching structured workout (`quickstart.md` Scenario A).

### Tests for User Story 1

- [X] T010 [P] [US1] Test `build_freestyle_external_id(session_date, workout_type)` in
      `tests/test_providers/test_calendar.py`: starts with `EXTERNAL_ID_PREFIX`, contains the date and
      workout type, unique across calls for the same inputs (random suffix)
- [X] T011 [P] [US1] Test `publish_freestyle_session()` in `tests/test_services/test_publication.py`:
      renders the DSL, hashes it, calls `create_event()` with the right payload, returns a `SessionOutcome`
      with `status="created"`; a response carrying `push_errors` deletes the just-created event and returns
      `status="refused"` (mirrors `publish_sessions()`'s existing handling)
- [X] T012 [P] [US1] Test the `freestyle:publish:<id>` callback in
      `tests/test_bot/test_chat_freestyle_publish.py`: tapping the current id writes the event, persists a
      `freestyle_published_entries` row, and edits the message to confirm; tapping a ***missing or stale***
      id (nothing pending, or a newer suggestion has since replaced it) shows a "no longer current" alert
      and writes nothing (US1 Acceptance Scenario 2, FR-005)
- [X] T013 [P] [US1] Test that a calendar-write failure edits the message to state the publish did not
      succeed, and leaves `pending_freestyle_id` intact so retapping is a legitimate retry (US1 Acceptance
      Scenario 3, FR-008), in `tests/test_bot/test_chat_freestyle_publish.py`

### Implementation for User Story 1

- [X] T014 [US1] Implement `build_freestyle_external_id(session_date, workout_type)` in
      `app/providers/intervals/calendar.py` (`contracts/single-session-write.md`) — depends on T010 existing
      as a failing test
- [X] T015 [US1] Implement `publish_freestyle_session(client, session_date, name, workout_type, steps,
      zones)` in `app/services/publication.py`: `render_dsl()` → `hash_session_content()` →
      `build_freestyle_external_id()` → `calendar.build_event_payload()` → `client.create_event()`, with the
      same `push_errors` handling `publish_sessions()` already has (depends on T014)
- [X] T016 [US1] Implement the `freestyle:publish:<id>` callback handler in `app/bot/routers/chat.py`:
      compare the tapped id against `pending_freestyle_id`; on mismatch/missing, alert "no longer current"
      and stop (FR-005); on match, compute zones from the athlete's profile
      (`app/engine/zones.py::compute_power_zones`/`compute_hr_zones` per `coaching_mode`, `research.md`
      Decision 7), call `publish_freestyle_session()`, and on success persist via
      `freestyle_publication_repo.create()` and clear `pending_freestyle_id`/`pending_freestyle_suggestion`
      from FSM data (depends on T004, T008, T015)
- [X] T017 [US1] On publish failure, edit the message to state it plainly (FR-008) and leave
      `pending_freestyle_id` untouched (depends on T016)

**Checkpoint**: User Story 1 is fully functional and independently testable — `quickstart.md` Scenario A
(and E) should pass.

---

## Phase 4: User Story 2 - Ask for something different before committing (Priority: P2)

**Goal**: Only the session the athlete actually confirms gets published — never an earlier one they'd
already moved past.

**Independent Test**: Ask for a session, ask for a different one, confirm, and check that only the second
session was published (`quickstart.md` Scenario B).

**No new production code** — this behavior already falls out of Foundational's id design (T006-T009) and
US1's stale-id check (T016): asking for a new suggestion naturally overwrites `pending_freestyle_id`
(Phase 2), and the callback handler already rejects a mismatched id (Phase 3). This story is pinned by
tests, not built from scratch — a legitimate, independently-verifiable increment in its own right.

### Tests for User Story 2

- [X] T018 [P] [US2] Test in `tests/test_bot/test_chat_freestyle_publish.py`: ask for a session (id A), ask
      for a different one (id B), tap **A**'s button — rejected, nothing published (US2 Acceptance Scenario
      1, `quickstart.md` Scenario B)
- [X] T019 [P] [US2] Test in `tests/test_services/test_publication.py` or the same bot test file: after
      publishing session A, asking for and publishing session B does not withdraw or alter the
      already-published entry for A (US2 Acceptance Scenario 2)

**Checkpoint**: User Stories 1 AND 2 both verified — `quickstart.md` Scenario B passes.

---

## Phase 5: User Story 3 - Change your mind after publishing (Priority: P3)

**Goal**: A published freestyle session can be withdrawn, the same way plan-mode published sessions
already can, without touching anything else on the calendar; a mode switch out of freestyle cleans up
automatically too (FR-011).

**Independent Test**: Publish a freestyle session, withdraw it, and confirm it disappears from the calendar
while everything else stays untouched (`quickstart.md` Scenario C); publish one, switch to goal mode, and
confirm it's withdrawn automatically (`quickstart.md` Scenario D).

### Tests for User Story 3

- [X] T020 [P] [US3] Test `withdraw_freestyle_publications()` in `tests/test_services/test_publication.py`:
      removes only not-yet-withdrawn `freestyle_published_entries` rows for that user, never touches
      `published_entries` (plan-mode) or anything without the `banister:` prefix (US3 Acceptance Scenario 2)
- [X] T021 [P] [US3] Test `/unpublish` with no active plan in `tests/test_bot/` (extend the existing
      publish-router test file): offers to withdraw freestyle entries instead of "Aucun plan actif" (US3
      Acceptance Scenario 1, `quickstart.md` Scenario C)
- [X] T022 [P] [US3] Test in `tests/test_bot/test_goal_change.py` (extend): switching from freestyle to
      goal mode withdraws any still-future `freestyle_published_entries` rows, mirroring the existing
      plan-entry withdrawal in the other direction (FR-011, `quickstart.md` Scenario D)

### Implementation for User Story 3

- [X] T023 [US3] Implement `withdraw_freestyle_publications(session, client, user)` in
      `app/services/publication.py` (mirrors `withdraw_all_publications()`) — depends on T020 existing as a
      failing test
- [X] T024 [US3] Extend `app/bot/routers/publish.py`'s `/unpublish` command and callback: when there is no
      active plan, look up `freestyle_publication_repo.get_active_for_user()` instead of replying "Aucun
      plan actif", with its own confirmation callback (e.g. `pub:withdrawall_freestyle`) reusing the
      existing confirm/cancel keyboard shape (depends on T023)
- [X] T025 [US3] Call `withdraw_freestyle_publications()` from `app/bot/routers/goal.py::_regenerate()`
      when `old_plan is None` (the freestyle → goal direction), alongside the existing plan-entry
      stale-calendar check (FR-011) — depends on T023

**Checkpoint**: All three user stories independently functional — `quickstart.md` Scenarios A–F pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T026 [P] Update `CLAUDE.md`: the new `freestyle_published_entries` table, the button/callback
      confirmation mechanism (and why it doesn't reuse `PENDING_MODIFICATION`), and the `/unpublish`
      extension — per the Development Workflow rule that `CLAUDE.md` is updated in the same change that
      introduces a new table or non-obvious business rule
- [X] T027 [P] Run `ruff check app/ tests/` and fix anything this feature introduced
- [X] T028 Run `pytest tests/` (full suite) and confirm no regression outside this feature's own new/modified
      tests
- [ ] T029 Walk through `quickstart.md` Scenarios A–F manually against a real Telegram bot and a connected
      intervals.icu test account

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup; **blocks every user story** — all three read/write the
  suggestion-id mechanism this phase builds
- **Polish (Phase 6)**: depends on all three user stories being complete

### User Story Dependencies

- **US1 (P1)**: depends on Foundational. No dependency on US2 or US3.
- **US2 (P2)**: depends on Foundational **and** US1's callback handler (T016) — the stale-id rejection US2
  tests is implemented there, not duplicated. Not independent of US1 the way US3 is, because US2 is
  specifically about a behavior of US1's own mechanism, not a separate capability.
- **US3 (P3)**: depends on Foundational (the table/repo, T002-T005) and, for T025 only, on spec 009's
  existing `_regenerate()` — does not depend on US1's callback handler or US2's tests.

### Within Each User Story

Tests before implementation; model/migration before repository before service before bot-layer wiring
(Foundational: T002 → T003 → T004; US1: T014 → T015 → T016 → T017).

### Parallel Opportunities

- T002, T004 (different files) once T002 exists for T004 to import
- T005, T009 (tests in different files) in parallel once their respective implementations exist
- T010, T011, T012, T013 (US1 tests, different files/cases) in parallel
- T018, T019 (US2 tests) in parallel
- T020, T021, T022 (US3 tests, different files) in parallel
- US3's table/repo work (T002-T005) has no runtime dependency on US1/US2's chat-layer work, so a second
  developer could build US3's withdrawal path while a first builds US1/US2, converging only at T024/T025

---

## Parallel Example: User Story 1

```bash
# Tests, together:
Task: "Test build_freestyle_external_id() in tests/test_providers/test_calendar.py"
Task: "Test publish_freestyle_session() in tests/test_services/test_publication.py"
Task: "Test the freestyle:publish callback in tests/test_bot/test_chat_freestyle_publish.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Setup → Foundational → US1
2. **STOP and VALIDATE**: `quickstart.md` Scenario A — an athlete can now actually load a freestyle
   suggestion onto their device. This is the headline gap the athlete flagged.
3. US2's tests validate a property of what's already built (cheap to add right after US1); US3 (withdrawal)
   is the natural next increment once athletes start accumulating freestyle publications they may not do.

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → test independently → MVP demo (publish and follow on a device)
3. US2 → test independently → demo (negotiation is provably safe, not just "seems fine")
4. US3 → test independently → demo (withdrawal, both manual and automatic on mode switch)

## Notes

- [P] tasks touch different files (or independent cases in the same file) and have no unmet dependency
- Commit after each task or logical group
- Verify each test fails before implementing the task that makes it pass
- T003 (migration) must be generated after T002 (model) exists, never hand-written
