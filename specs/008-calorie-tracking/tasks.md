---
description: "Task list for 008-calorie-tracking"
---

# Tasks: Daily Calorie Tracking

**Input**: Design documents from `/specs/008-calorie-tracking/` (spec.md, plan.md, research.md, data-model.md, contracts/nutrition-tools.md, quickstart.md)

**Prerequisites**: the 5-tool agentic chat pattern (`app/llm/tools.py` / `app/llm/chat.py`), the session-reminder scheduler shape (`app/main.py::_weekly_recap_scheduler`), and `/reset`'s purge (`app/db/repositories/user_repo.py::_PURGE_MODELS`) — all shipped. Constitution v1.1.0.

**Tests**: Included — quickstart.md names specific pytest files, and the codebase's established practice (spec 006/007 precedent) is that repository arithmetic and tool dispatch get tests even outside `app/engine/`. Tests are part of done.

**Organization**: Grouped by user story, in priority order (P1 → the three P2s in spec order → P3), after a shared Setup/Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- Every task names its exact file path

---

## Phase 1: Setup

- [X] T001 [P] Create skeleton files with module docstrings: `app/db/models/meal_entry.py`, `app/db/repositories/meal_entry_repo.py`, `scripts/nutrition_state.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The table, the migration, and the two repository functions (`create`, `daily_totals`) every user story needs.

**⚠️ CRITICAL**: No user story work begins until this phase is complete.

- [X] T002 Define `MealEntry` in `app/db/models/meal_entry.py` per data-model.md §Persisted — `Uuid` PK, `user_id` FK `ondelete="CASCADE"` indexed, `entry_date` (`Date`, not null), `entry_type` (`String(16)`), `meal_slot` (`String(16)`, nullable), `raw_description` (`Text`), `estimated_calories` (`Integer`), `TimestampMixin`, `Index("idx_meal_entries_user_date", "user_id", "entry_date")`
- [X] T003 [P] Export `MealEntry` from `app/db/models/__init__.py` (depends on T002)
- [X] T004 [P] Add `meal_entries: Mapped[list["MealEntry"]] = relationship(back_populates="user", cascade="all, delete-orphan")` to `User` in `app/db/models/user.py`, matching `session_logs`/`chat_messages`/`activities` style (depends on T002)
- [X] T005 Generate the Alembic migration for `meal_entries` via `alembic revision --autogenerate` in `migrations/versions/` (depends on T002, T004); verify it applies on a fresh DB
- [X] T006 Implement `DailyCalorieTotal` dataclass, `create()`, and `daily_totals(session, user_id, start_date, end_date)` in `app/db/repositories/meal_entry_repo.py` per data-model.md — `daily_totals` returns one row per day **with at least one entry**; a day with zero rows is absent from the result, never present as zero (FR-008) (depends on T002). **Also implemented `delete_for_date`/`get_latest_for_date`/`delete` in the same pass (T019/T032) — the file is small enough that writing all five together was more coherent than three separate edits.**
- [X] T007 [P] Export `meal_entry_repo` from `app/db/repositories/__init__.py` (depends on T006)
- [X] T008 [P] Extend the comment above `_PURGE_MODELS` in `app/db/repositories/user_repo.py` to name the new exclusion category — nutrition history is neither training data nor identity, kept indefinitely by explicit athlete request (research R5); `MealEntry` is **not** added to the tuple
- [X] T009 [P] Test in `tests/test_db/test_meal_entries.py` — `create()` persists every field including `raw_description` verbatim; `daily_totals()` sums multiple same-day entries correctly and omits a day with zero entries from its result; deleting the `User` cascades to `meal_entries` (depends on T002, T004, T006). **Found and fixed a real bug while writing this: `get_latest_for_date` (T032/US4) ordered by `created_at`, but `TimestampMixin`'s `server_default=func.now()` is second-resolution on SQLite — two entries logged in the same second tied and returned an arbitrary row. Fixed by setting `created_at` explicitly via `datetime.now(UTC)` in `create()`, the same pattern `guardrail_repo.py` already uses for its own recency-ordered rows.**

**Checkpoint**: table exists and migrated, core repo functions work, foundation ready.

---

## Phase 3: User Story 1 - Log what I ate and see the estimate (Priority: P1) 🎯 MVP

**Goal**: The athlete describes a meal in free-text chat and gets back a calorie estimate plus the day's running total, in one exchange.

**Independent Test**: Send one free-text meal description in Telegram; confirm a labelled estimate and a day total come back (quickstart Scenario 1).

### Tests for User Story 1

- [X] T010 [P] [US1] Test `log_meal` tool dispatch in `tests/test_llm/test_nutrition_tools.py` — a valid call persists an entry (`raw_description` = the literal user message, not a paraphrase) and returns `day_total_estimated_calories` matching an independently-run `daily_totals` query (contracts §1)
- [X] T011 [P] [US1] Test `log_meal` rejects `estimated_calories <= 0` or `> 8000` **without** writing to the database, returning `{"ok": false, ...}` (contracts §1), in `tests/test_llm/test_nutrition_tools.py`

### Implementation for User Story 1

- [X] T012 [US1] Add the `log_meal` schema to `TOOL_DEFINITIONS` in `app/llm/tools.py` per contracts/nutrition-tools.md §1 — added together with T023/T033 (one contiguous edit to the tool list)
- [X] T013 [US1] Thread `raw_message=user_message` through the `tool_executor` closure and `_execute_tool`'s signature in `app/llm/chat.py::run_chat` (research R1 — the closure currently has no access to the athlete's literal words) (depends on T012)
- [X] T014 [US1] Implement `_tool_log_meal()` in `app/llm/chat.py` — clamp `days_ago` to 0–2, compute `entry_date`, validate `estimated_calories` (1–8000, reject otherwise per contracts §1), `meal_slot` only when `entry_type=="meal"`, insert via `meal_entry_repo.create`, re-read the day total via `meal_entry_repo.daily_totals`, return per contracts §1 (depends on T006, T013). Includes the T020 (day-recap replace) branch — written together, one function.
- [X] T015 [US1] Dispatch `"log_meal"` to `_tool_log_meal` in `_execute_tool()` in `app/llm/chat.py` (depends on T014) — dispatched together with `undo_last_meal_entry`/`get_calorie_history` (T025/T035) in one edit
- [X] T016 [US1] Implement `scripts/nutrition_state.py --describe` (quickstart Scenario 0) — today's entries and running total via `meal_entry_repo.daily_totals`, mirroring `scripts/guardrail_state.py --describe` (depends on T006). Smoke-tested against the real dev DB after applying the migration (T005) — confirmed correct output for a week with nothing logged.

**Checkpoint**: US1 functional and independently testable/demoable — this is the MVP.

---

## Phase 4: User Story 2 - Log a whole day at once (Priority: P2)

**Goal**: A single end-of-day recap replaces that day's individual meal entries instead of summing with them.

**Independent Test**: Log meals individually, then send a day-recap for the same day; confirm the total reflects only the recap (quickstart Scenario 2).

### Tests for User Story 2

- [X] T017 [P] [US2] Test `meal_entry_repo.delete_for_date()` removes exactly that day's rows and returns the count deleted, in `tests/test_db/test_meal_entries.py -k replace` (depends on T006) — done together with T009 (Foundational)
- [X] T018 [P] [US2] Test `log_meal` with `entry_type="day_recap"` on a day with existing meal entries: prior entries are gone, the day total is the recap's figure alone (not summed), and the tool result has `replaced_existing_entries: true`, in `tests/test_llm/test_nutrition_tools.py` (depends on T014)

### Implementation for User Story 2

- [X] T019 [US2] Implement `delete_for_date(session, user_id, entry_date) -> int` in `app/db/repositories/meal_entry_repo.py` (depends on T006) — done together with T006
- [X] T020 [US2] In `_tool_log_meal()` (`app/llm/chat.py`): when `entry_type == "day_recap"`, call `delete_for_date` before inserting and set `replaced_existing_entries` in the result accordingly (FR-009) (depends on T014, T019) — done together with T014

**Checkpoint**: US1 + US2 functional — no double-counted totals.

---

## Phase 5: User Story 3 - Review calorie history (Priority: P2)

**Goal**: The athlete can ask for recent days' totals; an unlogged day is stated as such, never shown as zero.

**Independent Test**: Log entries on non-consecutive days, then ask for the week's history; confirm the gap day is reported as "nothing logged" (quickstart Scenario 3).

### Tests for User Story 3

- [X] T021 [P] [US3] Test `meal_entry_repo.daily_totals()` over a multi-day range returns correct per-day sums/counts and omits untouched days, in `tests/test_db/test_meal_entries.py` (depends on T006) — done together with T009 (Foundational)
- [X] T022 [P] [US3] Test `get_calorie_history` tool result includes every requested day, with `"logged": false` and **no** `total_calories` key for an empty day (contracts §3), in `tests/test_llm/test_nutrition_tools.py -k history`

### Implementation for User Story 3

- [X] T023 [US3] Add the `get_calorie_history` schema to `TOOL_DEFINITIONS` in `app/llm/tools.py` per contracts §3 — added together with T012/T033
- [X] T024 [US3] Implement `_tool_get_calorie_history()` in `app/llm/chat.py` — call `meal_entry_repo.daily_totals` over `[today - days + 1, today]`, fill every day not in the result with `{"date": ..., "logged": false}` (FR-008) (depends on T006)
- [X] T025 [US3] Dispatch `"get_calorie_history"` in `_execute_tool()` in `app/llm/chat.py` (depends on T023, T024) — dispatched together with T015/T035

**Checkpoint**: US1 + US2 + US3 functional.

---

## Phase 6: User Story 5 - Evening reminder if nothing was logged (Priority: P2)

**Goal**: A daily ~22:00 nudge if the athlete hasn't logged anything that day.

**Independent Test**: Confirm the pure selection logic includes a user with zero entries today and excludes one with at least one (quickstart Scenario 5), then a live check that Telegram delivery matches.

### Tests for User Story 5

- [X] T026 [P] [US5] Test the "which users need a reminder today" selection in `tests/test_services/test_nutrition_reminder.py` — a user with zero `meal_entries` rows for today is selected; a user with ≥1 entry (either `entry_type`) is excluded, even if logged minutes earlier (depends on T006)

### Implementation for User Story 5

- [X] T027 [US5] Extract the selection check as a small function callable independently of the sleep loop (depends on T006). **Deviation from the task's literal suggestion**: implemented as `app/services/nutrition_reminder.py::needs_reminder()` rather than inline in `app/main.py` — `app/main.py` instantiates a real `Bot`/`Dispatcher` at import time (`bot = create_bot()`), so a test importing it would need real Telegram credentials; no existing test does that (confirmed by grep). A tiny service module keeps T026 importable and matches Constitution III's bot/service split.
- [X] T028 [US5] Implement `_nutrition_reminder_scheduler(bot)` and `_run_nutrition_reminders(bot)` in `app/main.py` — mirrors `_weekly_recap_scheduler`'s fixed-time-sleep shape (research R4): compute the next 22:00 using the same fixed UTC+1 "CET" convention as `_run_session_reminders`, `asyncio.sleep` until then, select `User.is_active` (matching `_run_weekly_recap_broadcast`), skip anyone `needs_reminder` is `False` for, send the reminder message, loop (depends on T027). Verified: module imports cleanly, functions callable, no lint regression beyond baseline.
- [X] T029 [US5] Wire `_nutrition_reminder_scheduler` into `lifespan()` in `app/main.py` — create the task alongside `recap_scheduler_task`/`reminder_scheduler_task`, cancel it the same way on shutdown (depends on T028)

**Checkpoint**: US1 + US2 + US3 + US5 functional.

---

## Phase 7: User Story 4 - Correct a logged entry (Priority: P3)

**Goal**: Undo the most recent mis-logged entry from today.

**Independent Test**: Log a wrong entry, undo it, confirm the day total reverts; confirm undoing with nothing logged today is reported, not silently ignored (quickstart Scenario 4).

### Tests for User Story 4

- [X] T030 [P] [US4] Test `meal_entry_repo.get_latest_for_date()` and `delete()` in `tests/test_db/test_meal_entries.py -k undo` (depends on T006) — done together with T009 (Foundational); this test is what surfaced the `created_at` ordering bug fixed in T009
- [X] T031 [P] [US4] Test `undo_last_meal_entry`: removes the latest entry logged today and returns the recomputed day total; returns `{"ok": false, ...}` when nothing was logged today (contracts §2), in `tests/test_llm/test_nutrition_tools.py`

### Implementation for User Story 4

- [X] T032 [US4] Implement `get_latest_for_date(session, user_id, entry_date) -> MealEntry | None` and `delete(session, entry)` in `app/db/repositories/meal_entry_repo.py` (depends on T006) — done together with T006
- [X] T033 [US4] Add the `undo_last_meal_entry` schema to `TOOL_DEFINITIONS` in `app/llm/tools.py` per contracts §2 — added together with T012/T023
- [X] T034 [US4] Implement `_tool_undo_last_meal_entry()` in `app/llm/chat.py` — find and delete today's latest entry, recompute the day total via `daily_totals`, return per contracts §2 (depends on T032, T033)
- [X] T035 [US4] Dispatch `"undo_last_meal_entry"` in `_execute_tool()` in `app/llm/chat.py` (depends on T034) — dispatched together with T015/T025

**Checkpoint**: all five user stories functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T036 [P] Extend `tests/test_bot/test_reset.py` to assert `meal_entries` rows survive `purge_athlete_data` (research R5) — read `_PURGE_MODELS` directly rather than only asserting row counts (depends on T002, T008). Added `test_meal_entries_not_in_purge_models` (direct grep-style check) plus a row-count assertion in the exact-word deletion test.
- [X] T037 Update `CLAUDE.md` — added `meal_entries` to the SQLite tables table, a new "Suivi calorique (spec 008)" section, three new navigation rows, the `/reset` exclusion note (alongside `coach_voice`/`disclaimer_acknowledged_at`), `meal_entry_repo.py`/`nutrition_reminder.py` in the architecture tree, and bumped "5 outils LLM" → "8 outils LLM".
- [X] T038 Ran `ruff check app/ tests/ scripts/` — confirmed 227/227 violations before and after (zero new, verified via `git stash`/`git stash pop`, not by eyeballing). Ran the full suite: 496 passed, 19 skipped (Postgres unreachable, expected), plus one **pre-existing, unrelated** failure (`test_plan_builder.py::test_sessions_on_available_days_only`, a date-sensitive race-day session test that broke when the real calendar date advanced mid-session — confirmed unrelated: different file, untouched by this feature). The spec-008 subset (`test_meal_entries.py` + `test_nutrition_tools.py` + `test_nutrition_reminder.py` + `test_reset.py`) is 21 passed, 6 skipped, 0 failed. **Not performed** (no live Telegram/agent access in this environment): quickstart Scenario 1's live free-text-extraction check and Scenario 5's live reminder-delivery check — the deterministic halves of both (tool validation, selection logic) are fully covered by the automated tests above; the "does the model actually call the right tool on real phrasing" half needs a live session, same limitation spec 007's T051 recorded for its own live checks.

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)**. Everything in Phase 2 blocks every user story — the table and `create`/`daily_totals` are shared by all five.
- **US1 (Phase 3)** depends only on Foundational. MVP.
- **US2 (Phase 4)** depends on US1's `log_meal` tool/executor existing to add replace semantics to.
- **US3 (Phase 5)** depends only on Foundational's `daily_totals` — independent of US1/US2's tool wiring beyond reusing the same `TOOL_DEFINITIONS` list and `_execute_tool` dispatcher.
- **US5 (Phase 6)** depends only on Foundational's `daily_totals` — independent of the other stories; the reminder never calls `log_meal` or the history tool.
- **US4 (Phase 7)** depends only on Foundational — independent of US2/US3/US5, but naturally comes last since correcting an entry matters most once logging (US1) is real.
- **Polish (Phase 8)** depends on all five stories being done.

Within a phase, `[P]` tasks touch different files; unmarked tasks are sequential.

## Implementation Strategy

**MVP = US1 alone** (Phases 1–3). This is the whole point of the feature: describe a meal, get an estimate
and a running total. It's independently demoable the moment Phase 3 lands.

**Then US2** (Phase 4) — small, and closes the one real correctness risk (double-counting) named in the
spec's own edge cases before the feature sees real daily use.

**US3 and US5 (Phases 5–6) can be built in either order, or in parallel** — both depend only on Foundational's
`daily_totals`, not on each other or on US1/US2's tool wiring beyond sharing `TOOL_DEFINITIONS`. US3 (history)
is what makes logging worth doing more than once; US5 (reminder) is what keeps logging from lapsing on a busy
day — pick whichever matters more day-to-day first.

**US4 (Phase 7) last.** It's P3 for a reason: a correction mechanism only matters once there's real logged
data to correct, and `undo_last_meal_entry` is a small, self-contained addition once Foundational exists.

**Polish (Phase 8)** closes the loop the codebase's own workflow requires: the `/reset` exclusion must be
*provably* true (T036), not just true by omission, and `CLAUDE.md` must reflect the new table/tools/scheduler
in the same change that introduces them (Constitution Development Workflow).
