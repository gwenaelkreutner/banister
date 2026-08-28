---
description: "Task list for 005-planned-workout-push"
---

# Tasks: Publishing Planned Sessions to the Athlete's Calendar

**Input**: Design documents from `/specs/005-planned-workout-push/` (spec.md, plan.md, research.md, data-model.md, contracts/calendar-publication.md, quickstart.md)

**Prerequisites**: spec 004 (structured sessions) — this feature is unbuildable until `SessionSpec` carries `steps`.

**Tests**: Included — quickstart.md's scenarios name specific pytest files (`tests/test_providers/test_workout_dsl.py`, `tests/test_providers/test_calendar.py`, `tests/test_services/test_publication.py`), so tests are part of this feature's definition of done, not optional.

**Organization**: Tasks are grouped by user story (spec.md's US1–US5, priority order) after a shared Setup/Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Every task names its exact file path

## Path Conventions

Existing single-project layout (`app/`, `tests/`) — see plan.md's Project Structure for the exact new/modified files.

---

## Phase 1: Setup

**Purpose**: Skeleton files so every later task has somewhere to land.

- [X] T001 [P] Create skeleton files with module docstrings for: `app/providers/intervals/workout_dsl.py`, `app/providers/intervals/calendar.py`, `app/services/publication.py`, `app/bot/routers/publish.py`, `app/bot/keyboards/publish.py`, `app/db/models/publication.py`, `app/db/repositories/publication_repo.py`
- [X] T002 [P] Ensure `tests/test_providers/`, `tests/test_services/`, `tests/test_db/` exist and follow the project's existing test-package convention

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Storage, write verbs, and the DSL renderer every user story writes through.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Define `PublicationApproval` ORM model in `app/db/models/publication.py` per data-model.md §PublicationApproval (`id`, `user_id`, `plan_id`, `content_hash`, `horizon_start`/`horizon_end`, `session_count`, `status`, `requested_at`, `decided_at`)
- [X] T004 Define `PublishedEntry` ORM model in `app/db/models/publication.py` per data-model.md §PublishedEntry (`id`, `user_id`, `plan_id`, `approval_id` FK, `external_id` unique-per-user, `intervals_event_id`, `session_date`, `week_number`/`day_of_week`, `content_hash`, `published_at`, `withdrawn_at`) (same file as T003, sequential)
- [X] T005 Generate the Alembic migration for `publication_approvals` and `published_entries` via `alembic revision --autogenerate` in `migrations/versions/` (depends on T003, T004)
- [X] T006 [P] Add `_post`/`_put`/`_delete` to `app/providers/intervals/client.py`, reusing existing error classification (401/403 → `CredentialRejectedError`, 429 → `RateLimitedError`, 5xx/network → `TransientError`) per research.md R6
- [X] T007 Add `list_events`/`create_event`/`update_event`/`delete_event` to `app/providers/intervals/client.py`, targeting `/athlete/{id}/events` (depends on T006, same file)
- [X] T008 [P] Implement `render_dsl(steps)` in `app/providers/intervals/workout_dsl.py` — `Step`/`RepeatGroup` → workout DSL text per contracts/calendar-publication.md §1 (whole-minute durations, `%ftp` bounds from `Zone.lower_pct`/`upper_pct`, a repeat group renders its full inner unit including the final recovery); raises rather than emitting anything for a session with no steps (FR-009)
- [X] T009 [P] Test `render_dsl()` against research.md R3's verified DSL shape and the reference `workout_doc` fixture in contracts/calendar-publication.md §1, in `tests/test_providers/test_workout_dsl.py`
- [X] T010 [P] Implement `hash_content(session_date, name, rendered_dsl)` — `SHA-256(session_date | name | rendered_DSL_text)` — in `app/services/publication.py` per data-model.md §Content hashing
- [X] T011 [P] Implement `publication_repo.py`: `create_approval`, `get_approval`, `mark_approved`, `mark_declined`, `create_published_entry`, `update_published_entry`, `get_active_entries_for_plan`, `get_entry_by_external_id`, `mark_withdrawn` in `app/db/repositories/publication_repo.py` (depends on T003, T004)

**Checkpoint**: Storage, write verbs, and the DSL contract are all in place and tested — user story work can begin.

---

## Phase 3: User Story 1 - The session is on the watch in the morning (Priority: P1) 🎯 MVP

**Goal**: Approve a week of sessions and have them appear in the athlete's calendar in a form their device can execute.

**Independent Test**: Approve a week of sessions and confirm they appear in the athlete's training calendar in executable form (per quickstart.md Scenario 1).

### Tests for User Story 1

- [X] T012 [P] [US1] Test event payload construction (`start_date_local`, `category="WORKOUT"`, `type="Ride"`, `name`, `external_id`, `description`) per contracts/calendar-publication.md §2, in `tests/test_providers/test_calendar.py`
- [X] T013 [P] [US1] Test that a session with no steps is refused and reported, never published empty (FR-009), in `tests/test_services/test_publication.py`

### Implementation for User Story 1

- [X] T014 [US1] Implement `build_external_id(plan_id, session_date, workout_type, week, dow)` in `app/providers/intervals/calendar.py` per data-model.md §External id (`banister:<plan_id_short>:<yyyy-MM-dd>:<workout_type>-<week>-<dow>`)
- [X] T015 [US1] Implement `publish_sessions(client, plan, horizon_start, horizon_end)` in `app/providers/intervals/calendar.py` — renders each session's DSL (T008), builds the event payload (T014), calls `create_event()`; a session without steps is collected as a refusal, never raised past the batch (FR-009) (depends on T007, T008, T014)
- [X] T016 [US1] Implement `request_publication(session, user, plan)` in `app/services/publication.py` — computes the horizon (current + following week, FR-010), builds the approval-request text listing every session and date per contracts/calendar-publication.md §3 including the device-forwarding caveat (FR-012, SC-009), and creates a `pending` `PublicationApproval` via `publication_repo` (depends on T010, T011)
- [X] T017 [US1] Implement `/publish` command and the approve/decline inline keyboard (`pub:` prefix) in `app/bot/routers/publish.py` + `app/bot/keyboards/publish.py`, rendering the request from T016
- [X] T018 [US1] Wire the approve callback: call `calendar.publish_sessions()` (T015) and report the per-session outcome to the athlete, in `app/bot/routers/publish.py`
- [X] T019 [US1] Register `publish_router` in `app/bot/setup.py`, before `chat_router`

**Checkpoint**: User Story 1 is independently functional — a week can be approved and published in executable form.

---

## Phase 4: User Story 2 - Nothing appears that the athlete did not agree to (Priority: P1)

**Goal**: Every write path is gated behind a recorded, content-bound approval.

**Independent Test**: Attempt every path that could produce a write and confirm each is gated behind a recorded approval (quickstart.md Scenario 2).

### Tests for User Story 2

- [X] T020 [P] [US2] Test that every write path refuses without a stored `approved` `PublicationApproval` whose `content_hash` matches the current plan (FR-001, FR-004), in `tests/test_services/test_publication.py -k approval`
- [X] T021 [P] [US2] Test that declining writes nothing and does not re-ask unprompted (FR-003), in `tests/test_services/test_publication.py`

### Implementation for User Story 2

- [X] T022 [US2] Implement `authorize_publication(approval_id, plan)` in `app/services/publication.py` — recomputes the plan's current content hash and refuses (fresh approval required) if it no longer matches the approval's `content_hash` (FR-004); call this before every write reaches `calendar.py` (depends on T016)
- [X] T023 [US2] Wire the decline callback in `app/bot/routers/publish.py`: mark the approval `declined` via `publication_repo`, confirm nothing was written, no further unprompted asking (FR-003)
- [X] T024 [US2] Thread `approval_id` through `publish_sessions()` onto every `PublishedEntry` it writes (FR-005) (depends on T015, T022)
- [X] T025 [US2] Build the post-publication report — per-session ✅/❌ list, never a blanket success (FR-006) per contracts/calendar-publication.md §3 — in `app/services/publication.py`, consumed by T018's callback
- [X] T026 [US2] Enumerate, by grep, every call site reaching `create_event`/`update_event`/`delete_event` and confirm each sits behind `authorize_publication()` (quickstart.md Scenario 2: "a grep, not a vibe") — record the enumeration in the commit message

**Checkpoint**: US1 + US2 together are the MVP — publication only ever happens with recorded, content-bound consent.

---

## Phase 5: User Story 3 - Publishing twice does not duplicate anything (Priority: P2)

**Goal**: Republishing a period converges instead of accumulating duplicates.

**Independent Test**: Publish the same period repeatedly and confirm the calendar converges rather than accumulating (quickstart.md Scenario 3).

### Tests for User Story 3

- [X] T027 [P] [US3] Test that five consecutive publications of the same period produce exactly one entry per session (SC-003), in `tests/test_providers/test_calendar.py`
- [X] T028 [P] [US3] Test that a foreign (`cycling-coach:`-prefixed) entry survives every republication untouched (FR-015, SC-004), in `tests/test_providers/test_calendar.py`
- [X] T029 [P] [US3] Test that an interrupted publication, retried, reaches the same calendar state as an uninterrupted one (FR-016, SC-007), in `tests/test_services/test_publication.py`

### Implementation for User Story 3

- [X] T030 [US3] Implement idempotent diffing in `publish_sessions()`: `list_events()` the target window, filter to `external_id.startswith("banister:")`, index by `external_id`, `update_event()` what already exists (matched via `publication_repo.get_entry_by_external_id`), `create_event()` only what does not (research.md R2) (depends on T007, T015)
- [X] T031 [US3] Make publication resumable: before writing, skip sessions whose `PublishedEntry` already exists with a matching `content_hash` for this approval; write only what's missing or changed (FR-016, FR-017) (depends on T011, T030)
- [X] T032 [P] [US3] Implement `scripts/calendar_state.py --describe` — lists calendar events grouped by `external_id` prefix (`banister:` / `cycling-coach:` / other), per quickstart.md Scenario 0
- [X] T033 [P] [US3] Implement `scripts/publish_horizon.py --approve-for-test` — dev/test utility running request→auto-approve→publish in one call, per quickstart.md Scenario 3

**Checkpoint**: US1–US3 independently testable — republication is safe and observably converges.

---

## Phase 6: User Story 4 - The calendar follows the plan (Priority: P2)

**Goal**: A plan change, once re-approved, is reflected in the calendar; removed sessions are withdrawn; staleness is never silent.

**Independent Test**: Publish a week, modify the plan, and confirm the calendar reflects the modification after approval (quickstart.md Scenario 4).

### Tests for User Story 4

- [X] T034 [P] [US4] Test that after a plan change and re-approval, every future published entry matches the revised plan (SC-005), in `tests/test_services/test_publication.py`
- [X] T035 [P] [US4] Test that a session removed from the plan has its entry withdrawn, not left behind (FR-019), in `tests/test_services/test_publication.py`
- [X] T036 [P] [US4] Test that past-dated sessions are never rewritten when the plan changes (FR-011, FR-022), in `tests/test_services/test_publication.py`

### Implementation for User Story 4

- [X] T037 [US4] Implement `check_divergence(plan, published_entries)` in `app/services/publication.py` — "plan moved ahead of calendar" kind: current session's content hash ≠ `PublishedEntry.content_hash` (FR-020) per data-model.md §Divergence
- [X] T038 [US4] Surface plan/calendar divergence in the coach's conversational context so the athlete is told the calendar is out of date rather than it looking current (FR-020) — wherever plan-state context is already assembled for the LLM (`app/llm/tools.py` / `app/llm/prompts.py`)
- [X] T039 [US4] Implement `withdraw_entry(entry)` in `app/providers/intervals/calendar.py` — calls `delete_event()`, marks `withdrawn_at`; call it for every session no longer present in the plan (FR-019)
- [X] T040 [US4] Exclude past-dated sessions from both the publish and republish/diff paths (FR-011, FR-022) (depends on T030)
- [X] T041 [US4] Implement withdraw-all — `/publish` withdrawal command with confirmation step in `app/bot/routers/publish.py` — calls `withdraw_entry` for every live `PublishedEntry`, reports 100%/0% (FR-021, SC-006)
- [X] T042 [P] [US4] Extend `scripts/calendar_state.py` with `--withdraw-all --confirm`, per quickstart.md Scenario 6 (depends on T032, T041)

**Checkpoint**: US1–US4 independently testable — the calendar tracks the plan, and staleness is always announced.

---

## Phase 7: User Story 5 - The athlete's own edits are respected (Priority: P3)

**Goal**: An athlete-made edit or deletion is noticed, never silently overwritten or recreated.

**Independent Test**: Edit a published entry at the calendar, then republish, and confirm the edit is not silently discarded (quickstart.md Scenario 5).

### Tests for User Story 5

- [X] T043 [P] [US5] Test that an athlete-edited entry is not silently overwritten — the athlete is told and decides (FR-023, SC-008), in `tests/test_services/test_publication.py`
- [X] T044 [P] [US5] Test that an athlete-deleted entry is not silently recreated (FR-024), in `tests/test_services/test_publication.py`
- [X] T045 [P] [US5] Test that a completed activity is never altered by publication (FR-025), in `tests/test_services/test_publication.py`

### Implementation for User Story 5

- [X] T046 [US5] Resolve research.md's open question 3 (does the event's `updated` timestamp change only on athlete edits, or also on our own writes?) with one probe, then implement `detect_athlete_edit(remote_event, entry)` in `app/services/publication.py` accordingly (FR-023)
- [X] T047 [US5] On a detected athlete edit, surface the conflict instead of overwriting — extend the republish flow in `app/bot/routers/publish.py` to ask the athlete for a decision (FR-023)
- [X] T048 [US5] Implement athlete-deletion detection in `app/services/publication.py`: `external_id` absent from the remote window but a live (non-withdrawn) `PublishedEntry` exists → surface, never recreate (FR-024)
- [X] T049 [US5] Guard every write path against ever touching an event whose `category` is not `WORKOUT` (i.e. a completed activity) (FR-025)

**Checkpoint**: All five user stories are independently testable.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T050 [P] Ensure a mid-publication connection failure leaves the calendar coherent (already-written entries recorded) and is reported to the athlete rather than failing silently (FR-026) — verify `TransientError` handling in `publish_sessions()`
- [ ] T051 [P] Implement per-session rejection handling: a session the calendar rejects as unrepresentable is reported specifically and does not abandon the rest of the publication (FR-028) — inspect the event's `push_errors` field per research.md open question 2
- [ ] T052 [P] Document the publication quota margin by arithmetic (a full horizon ≈10 requests against ~5000/day) as a code comment near `publish_sessions()` — deliberately not stress-tested (FR-027, SC-010, research.md open question 1)
- [ ] T053 Run `pytest tests/` and `ruff check app/ tests/`; fix any violation this feature introduced
- [ ] T054 Run the full quickstart.md scenario sequence (0–7) against the real account; confirm every "Definition of done" item, including the foreign `cycling-coach:` entry surviving every scenario
- [ ] T055 Update `CLAUDE.md`: architecture tree (new modules), Tables SQLite (two new tables), Navigation rapide entries, and the spec-004→005 status line at the top of the document
- [ ] T056 Update `docs/ARCHITECTURE.md` if it separately documents provider/service/bot layering, per project convention for significant new modules

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)**: no user story work starts before Phase 2 completes.
- **US1 (Phase 3)** depends only on Foundational. It is the MVP by itself, though a real athlete would want US2's hardening before trusting it.
- **US2 (Phase 4)** depends on US1's `publish_sessions`/`request_publication` existing to gate.
- **US3 (Phase 5)** depends on US1's `publish_sessions` (extends it with diffing) — independent of US2's approval hardening except that it reuses the same call sites.
- **US4 (Phase 6)** depends on US3's diffing (`get_active_entries_for_plan`, idempotent update) to know what to withdraw or leave alone.
- **US5 (Phase 7)** depends on US3's window-read (`list_events`) to detect edits/deletions.
- **Polish (Phase 8)** depends on all user stories.

Within a phase, `[P]`-marked tasks touch different files and may run in parallel; unmarked tasks in the same phase are sequential (usually same-file edits or a direct functional dependency noted in the task).

## Implementation Strategy

**MVP = US1 + US2** (Phases 1–4, T001–T026): a session can be approved and published, and every write path is verifiably gated. This alone changes what the product is — sessions reach the athlete's device instead of staying on their phone — while carrying the non-negotiable consent guarantee (spec.md's "least tolerance for probably fine").

**Then incrementally**: US3 (idempotence) before the feature is used a second time in anger, US4 (plan-following) once plans start changing after publication, US5 (respecting athlete edits) once the feature has been adopted enough that an athlete starts touching their own calendar.

Each user story phase ends at a checkpoint where the feature built so far is independently demonstrable — per quickstart.md's numbered scenarios, which this task list follows one-to-one.
