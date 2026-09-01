---
description: "Task list for 007-first-run-and-goals"
---

# Tasks: First Run, Goals, and Coach Voice

**Input**: Design documents from `/specs/007-first-run-and-goals/` (spec.md, plan.md, research.md, data-model.md, contracts/first-run.md, quickstart.md)

**Prerequisites**: spec 002 (intervals.icu client), spec 005 (`check_divergence`, approval pattern), spec 006 (`DISCLAIMER_TEXT`). All shipped. Constitution v1.1.0.

**Tests**: Included — quickstart.md names specific pytest files and the `load_persona` regression is a real user-visible defect. Tests are part of done.

**Organization**: Grouped by user story (US1–US6, priority order) after a shared Setup/Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- Every task names its exact file path

---

## Phase 1: Setup

- [X] T001 [P] Create skeleton files with module docstrings: `app/providers/intervals/athlete_profile.py`, `app/services/coach_voice.py`, `app/bot/routers/goal.py`, `app/bot/routers/reset.py`, `app/bot/routers/voice.py`, `scripts/athlete_profile.py`
- [X] T002 [P] Create the new test directories with the project's `__init__.py` convention: `tests/test_bot/`, `tests/test_core/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The two columns, the persona-schema fix, and the profile reader every story depends on.

**⚠️ CRITICAL**: No user story work begins until this phase is complete.

- [X] T003 Add `coach_voice` (`String(64)`, nullable) and `disclaimer_acknowledged_at` (`UtcDateTime`, nullable) to `User` in `app/db/models/user.py` per data-model.md §Persisted
- [X] T004 Generate the Alembic migration for the two columns via `alembic revision --autogenerate` in `migrations/versions/` (depends on T003); test it applies on a fresh DB and on the live `data/banister.db`
- [X] T005 In `app/config.py`: change `persona` default from `"coach-default"` to `"pace"` (research R2 — pace.yaml is already the French voice matching the live coach; making it the resolved default is what stops the wiring change from regressing behaviour). No `persona.py` change needed — an earlier research draft wrongly claimed the loader was broken; it works
- [X] T006 [P] Test in `tests/test_core/test_persona.py` — `load_persona()` returns a `Persona` with non-empty `system_prompt` and `ux_prompt` for **every** file in `personas/`; an unknown id and a file missing a required field both raise `PersonaNotFoundError`; `pace` resolves and is `language: fr`
- [X] T007 [P] Implement `read_athlete_profile(client) -> ReadProfile` in `app/providers/intervals/athlete_profile.py` per contracts/first-run.md §1 — thresholds from `sportSettings[]` selecting the entry whose `types` contains a cycling type; age from `icu_date_of_birth`; `icu_resting_hr` labelled as a profile default; every field carries `origin` + optional `as_of`; a field the source lacks is **absent**, never defaulted (FR-008); no cycling FTP → FTP absent + HR-mode flag (FR-009)
- [X] T008 [P] Test `read_athlete_profile()` in `tests/test_providers/test_athlete_profile.py` against a captured `GET /athlete` fixture (this account: FTP 290, LTHR 182, max_hr 202, DOB 1990-01-01) and against a fixture with no cycling `sportSettings` entry (FR-009 path)
- [X] T009 [P] Implement `scripts/athlete_profile.py --describe` — prints the confirmation-screen values with origins (quickstart Scenario 0), read-only (depends on T007)
- [X] T010 [P] Add `user_repo.set_coach_voice(session, user_id, voice_id | None)`, `user_repo.ack_disclaimer(session, user_id)`, and `user_repo.purge_athlete_data(session, user_id)` in `app/db/repositories/user_repo.py` — purge deletes `session_logs` / `chat_messages` / `activities` / `training_plans` / `athlete_profiles` / `weekly_adherence` / publication rows for the user, keeps the `User` row (telegram_id, first_name, coach_voice, disclaimer_acknowledged_at, reminder prefs); issues no outbound call (depends on T003)

**Checkpoint**: schema migrated, `load_persona` fixed and tested, the source reader works, the repo verbs exist.

---

## Phase 3: User Story 1 - Setup is a confirmation, not an interview (Priority: P1) 🎯 MVP

**Goal**: First-run setup shows what was read, then asks only goal + date + intended volume + constraints.

**Independent Test**: Connect a complete account, confirm setup completes with one screen + a few questions (quickstart Scenario 1).

### Tests for User Story 1

- [X] T011 [P] [US1] Test the confirmation flow in `tests/test_bot/test_setup_confirm.py` — after `read_athlete_profile`, the FSM presents `CONFIRM_PROFILE` before any question about the athlete; on `✅`, the only states entered are goal/date/volume/constraints; age is never asked; `hr_rest` comes from `icu_resting_hr` not the hard-coded 60
- [X] T012 [P] [US1] Test that `_build_profile` marks source-derived values `ftp_source`/`hr_max_source`/`hr_rest_source == "source"` and stamps `read_from_source_at` (FR-005, SC-003), in `tests/test_bot/test_setup_confirm.py`

### Implementation for User Story 1

- [X] T013 [US1] Add `CONFIRM_PROFILE` and `CORRECT_VALUE` to `SetupStates` in `app/bot/states.py`
- [X] T014 [US1] Build the confirmation screen renderer in `app/bot/routers/setup.py` — the table from contracts/first-run.md §2 with an origin line per value and an age where the source dates it; `[✅ Tout est bon]` / `[✏️ Corriger une valeur]` keyboard (`setup:confirm:*`)
- [X] T015 [US1] Rework `cmd_setup` / the early `SetupStates` in `app/bot/routers/setup.py` — on `/setup` with no active plan: call `read_athlete_profile`, store the `ReadProfile` in FSM data, enter `CONFIRM_PROFILE`. Remove the SPORT / POWER / AGE questions from the first-run path (they are read)
- [X] T016 [US1] After `✅`: enter the goal/date/volume/constraints sub-flow (reuse existing GOAL/DATE/VOLUME states; keep the constraints question). VOLUME text: "combien d'heures tu **veux** t'entraîner" — with the actual recent volume shown as a reference (FR-003, spec Assumptions)
- [X] T017 [US1] Rework `_build_profile()` in `app/bot/routers/setup.py` to take the confirmed `ReadProfile` + the four answers — FTP/LTHR/max_hr/weight/sex/age from the read profile with `*_source="source"`; `hr_rest` from `icu_resting_hr`; level still inferred from intended hours (unchanged heuristic); `read_from_source_at` stamped
- [X] T018 [US1] FR-010 path: when `wellness` < ~14 days and `activities` < ~4 weeks, seed CTL with `estimate_initial_ctl(tss_from_weekly_hours(intended_hours))` and have `_finalize_setup` say so — extend the existing fitness-seed block, add the disclosure line
- [X] T019 [US1] "What the plan was built from" recap after generation (FR-004) — a short block listing every input and its origin, appended to the `_finalize_setup` success message or shown on `/plan`

**Checkpoint**: US1 functional — a connected athlete confirms one screen and answers four things.

---

## Phase 4: User Story 2 - Nothing is assumed silently (Priority: P1)

**Goal**: A read value shows its origin and age; a correction goes to the source or the athlete is sent there — never a hidden local override.

**Independent Test**: Present a stale value, correct it, confirm no local divergence (quickstart Scenario 2).

### Tests for User Story 2

- [X] T020 [P] [US2] Test the correction flow in `tests/test_bot/test_setup_confirm.py` — `✏️ Corriger` → pick FTP → enter value → the athlete is shown `290 → 305` and told the value lives at intervals.icu; with no write-back endpoint, setup proceeds on **290** and states the divergence; the stored profile FTP is 290, not 305 (FR-007, SC-004)
- [X] T021 [P] [US2] Test that a source value missing (no cycling FTP) makes setup **ask** rather than default, and the coach states it runs on HR (FR-008, FR-009), in `tests/test_bot/test_setup_confirm.py`

### Implementation for User Story 2

- [X] T022 [US2] Implement the `CORRECT_VALUE` state in `app/bot/routers/setup.py` — list the editable read values; on selection, prompt for the new value; render the `from → to` + "cette valeur vit dans ton compte intervals.icu" message (contracts §3)
- [X] T023 [US2] FR-007 default path: after a correction, do **not** write the new value into the profile; keep the source's current value, set `read_from_source_at`, and add "j'utilise {source_value} tant qu'intervals.icu n'est pas à jour — change-le dans Réglages → Sport → {field}" to the confirmation summary. Return to `CONFIRM_PROFILE`
- [X] T024 [US2] **[DEFERRED — no separate authorisation given]** The write-back probe writes to the athlete's intervals.icu *account settings* (not a deletable calendar event) and was not authorised this session. FR-007 is the shipped correction path and is complete on its own: a correction is captured, the source's value is kept, the athlete is directed to change it at intervals.icu, and no divergent local value is stored (SC-004 holds). `client.update_athlete_field()` and the approval-gated write-back branch are left for a session that authorises the probe — same fence as spec 005's events-API verification.
- [X] T025 [US2] FR-009 rendering: when the read profile has no cycling FTP, the confirmation screen omits the FTP row, `coaching_mode` is `"hr"`, and `_finalize_setup` states "je pilote sur la fréquence cardiaque, je n'ai pas de FTP mesurée"

**Checkpoint**: US1 + US2 — the confirmation is honest end to end; no silent inference, no local override.

---

## Phase 5: User Story 3 - Changing the goal does not erase the athlete (Priority: P2)

**Goal**: `/goal` regenerates the plan from current fitness, keeps all history, surfaces stale calendar entries.

**Independent Test**: Change the goal on a populated deployment, confirm row counts survive and the new plan reflects current fitness (quickstart Scenario 3).

### Tests for User Story 3

- [X] T026 [P] [US3] Test `/goal` in `tests/test_bot/test_goal_change.py` — `session_logs` / `weekly_adherence` / `chat_messages` / `activities` counts identical before and after (SC-005); the new plan's week-1 TSS target is derived from current CTL, not zero (FR-012); sport / FTP / age / constraints are not re-asked (FR-013)
- [X] T027 [P] [US3] Test that a past goal date is rejected and a date < 21 days / > 365 days is challenged (FR-015), in `tests/test_bot/test_goal_change.py`
- [X] T028 [P] [US3] Test that when published calendar entries exist under the old plan, `/goal` surfaces the divergence via spec 005's `check_divergence` (FR-014), in `tests/test_bot/test_goal_change.py`

### Implementation for User Story 3

- [X] T029 [US3] Implement `/goal` in `app/bot/routers/goal.py` — requires an active plan; silently calls `read_athlete_profile` (no confirmation screen); asks goal type + date, and volume only on an explicit "je veux changer mon volume"; reuses `SetupStates.GOAL`/`DATE` or its own states
- [X] T030 [US3] Goal-date validation in `app/bot/routers/goal.py` — reject `target_date <= today`; challenge (ask to confirm) if `< today + 21d` ("trop court pour un vrai bloc") or `> today + 365d` ("si loin, le plan est surtout de la spéculation — vise un point de passage plus proche") (FR-015)
- [X] T031 [US3] Plan regeneration in `app/bot/routers/goal.py` — build the profile from the re-read source + the new goal (keep every other field), `generate_plan()`, `deactivate_all_for_user` + `plan_repo.create` (history rows keep pointing at the now-inactive plan, as `/setup` already does), `profile_repo.update` in place
- [X] T032 [US3] Post-regeneration: call spec 005's `check_divergence(new_schema, live_published_entries)` and, if non-empty, tell the athlete the calendar is stale and to relance `/publish` (FR-014); reuse `describe_divergence_for_coach` phrasing
- [X] T033 [US3] "What changed / what carried over" summary in `app/bot/routers/goal.py` (FR-016) — new weeks count + periodisation shape vs old; counts of kept sessions / adherence weeks / settings
- [X] T034 [US3] Register `goal_router` in `app/bot/setup.py` before `chat_router`

**Checkpoint**: US1–US3 — a goal change is a light, non-destructive re-plan.

---

## Phase 6: User Story 4 - Starting over is possible and deliberate (Priority: P2)

**Goal**: `/reset` lists exactly what will be discarded, requires a typed confirmation, deletes completely, never touches the source.

**Independent Test**: `/reset` and confirm the discard is explicit, warned, complete, and local-only (quickstart Scenario 4).

### Tests for User Story 4

- [X] T035 [P] [US4] Test `/reset` in `tests/test_bot/test_reset.py` — the warning lists real counts; typing anything but the exact word deletes nothing (FR-019, SC-006); typing the word deletes every listed row, verified by re-query (SC-006); `coach_voice` and `disclaimer_acknowledged_at` survive
- [X] T036 [P] [US4] Test that `purge_athlete_data` / the reset path import no `IntervalsClient` method — an AST/grep assertion (FR-020, SC-007), in `tests/test_bot/test_reset.py`

### Implementation for User Story 4

- [X] T037 [US4] Implement `/reset` in `app/bot/routers/reset.py` — gather counts (`session_logs`, `chat_messages`, `weekly_adherence`, active plan, profile), render the itemised warning from contracts §4 with the "PAS touché" list, enter a confirm state expecting the literal `SUPPRIMER`
- [X] T038 [US4] Confirm handler — exact match on `SUPPRIMER` → `user_repo.purge_athlete_data` + `state.clear()` + reset `onboarding_completed_at` to null so the next `/setup` is a genuine first run; anything else → "rien n'a été supprimé" and exit (FR-019)
- [X] T039 [US4] Register `reset_router` in `app/bot/setup.py` before `chat_router`

**Checkpoint**: US1–US4 — goal change and start-over are distinct, and neither is dangerous.

---

## Phase 7: User Story 5 - The athlete picks who is coaching them (Priority: P2)

**Goal**: `/voice` selects a persona; the next coach message follows it; it persists; unresolvable falls back and says so.

**Independent Test**: Change the voice, confirm subsequent output follows it, repeatedly and across a restart (quickstart Scenario 5).

### Tests for User Story 5

- [ ] T040 [P] [US5] Test `resolve_voice()` in `tests/test_llm/test_voice_wiring.py` — `coach_voice` set → that persona; `None` → `settings.persona`; unresolvable id → `coach-default` **and** a flag/marker that the caller renders a fallback notice (FR-024, FR-026)
- [ ] T041 [P] [US5] Test that the chat path uses `resolve_voice(user)` — `build_ux_system_prompt` / `COACH_SOUL` output changes with the column (FR-023), in `tests/test_llm/test_voice_wiring.py`

### Implementation for User Story 5

- [ ] T042 [US5] Implement `resolve_voice(user) -> tuple[Persona, bool]` in `app/services/coach_voice.py` — reads `user.coach_voice or settings.persona`, `load_persona` with a `try/except PersonaNotFoundError` → `load_persona("coach-default")`; second element is "fell back" (FR-024, FR-026)
- [ ] T043 [US5] Rewrite `personas/coach-default.yaml` in French to match the live "Pace" voice (from `COACH_SOUL` + `build_ux_system_prompt`) so the chat path does not regress; this is the default
- [ ] T044 [P] [US5] Write `personas/analyste.yaml` (terse, metrics-first, French) and `personas/zen.yaml` (calm, encouraging, French) — genuinely different voices, each with a clear `voice:` descriptor for `/voice` (FR-022)
- [ ] T045 [US5] Wire `app/llm/tools.py::build_system_prompt` and `app/llm/prompts.py::build_ux_system_prompt` to take the resolved persona (passed from `chat.py` via `resolve_voice(user)`) instead of the inline `COACH_SOUL` / "Tu t'appelles Pace" text — the inline text becomes the fallback persona's content, not a second identity
- [ ] T046 [US5] Move the "Banister"-identity prompts (`WEEKLY_RECAP_SYSTEM_PROMPT`, `PLAN_SYSTEM_PROMPT`, `WEEK_SYSTEM_PROMPT`, `COACH_BLOCKS_SYSTEM_PROMPT`) onto the resolved persona too — one selected voice everywhere; `_MODE_PERSONA` (narrative modes) stays a separate axis, untouched (spec Assumptions)
- [ ] T047 [US5] Implement `/voice` in `app/bot/routers/voice.py` — list every `personas/*.yaml` with `name` + `voice`; a button per persona (`voice:set:<id>`); on select, `user_repo.set_coach_voice` + confirm; available any time (FR-021)
- [ ] T048 [US5] Register `voice_router` in `app/bot/setup.py` before `chat_router`

**Checkpoint**: US1–US5 — the coach speaks in the athlete's chosen voice, and the mechanism that was broken now works.

---

## Phase 8: User Story 6 - The athlete knows what they are using (Priority: P3)

**Goal**: The disclaimer precedes the first coaching advice and is shown once.

**Independent Test**: Confirm the statement appears before the first coaching interaction and is not repeated (quickstart Scenario 6).

### Tests for User Story 6

- [ ] T049 [P] [US6] Test in `tests/test_bot/test_setup_confirm.py` — `_finalize_setup` sends `DISCLAIMER_TEXT` and sets `disclaimer_acknowledged_at` when it is null; on a second run with the flag set, it is **not** sent (FR-028)

### Implementation for User Story 6

- [ ] T050 [US6] Gate the disclaimer in `app/bot/routers/setup.py` and `app/bot/routers/goal.py` — send `prompts.DISCLAIMER_TEXT` only when `user.disclaimer_acknowledged_at is None`, then `user_repo.ack_disclaimer` (FR-027, FR-028); remove the unconditional send added in spec 006 T054

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T051 [P] Run the full quickstart.md scenario sequence (0–6) on the real account; confirm every "Definition of done" item, especially setup under 3 minutes (SC-010), zero readable attributes asked (SC-002, counted), `/goal` row-count preservation (SC-005), and `/reset` issuing zero outbound calls
- [ ] T052 [P] Run `pytest tests/` and `ruff check app/ tests/`; fix any violation this feature introduced
- [ ] T053 Update `CLAUDE.md` — the `/setup` flow section (now read→confirm→ask-less), new `/goal` `/reset` `/voice` commands + their routers in the registration order, `users.coach_voice` / `disclaimer_acknowledged_at` columns, `resolve_voice()` and the personas-now-wired note (removes the "load_persona construit mais JAMAIS appelé" caveat), Navigation rapide entries, and the spec-006→007 status line
- [ ] T054 Update the "Refonte open source en cours" table at the top of `CLAUDE.md` — spec 007 done; if all of 001–007 are now complete, replace the "refonte en cours" framing with "refonte terminée" and note what remains (`docs/ARCHITECTURE.md` rewrite, source write-back if T024's probe was deferred)

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)**. T005–T006 (the `load_persona` fix) is independent of the rest of Phase 2 and is the lowest-risk high-value change — do it first.
- **US1 (Phase 3)** depends only on Foundational (T007 reader, T017 profile builder). MVP.
- **US2 (Phase 4)** depends on US1's confirmation flow existing to add correction to.
- **US3 (Phase 5)** depends on US1's `_build_profile` rework and reuses spec 005's `check_divergence` (already shipped).
- **US4 (Phase 6)** depends only on Foundational's `purge_athlete_data`.
- **US5 (Phase 7)** depends on Foundational's `load_persona` fix; otherwise independent.
- **US6 (Phase 8)** depends on Foundational's `disclaimer_acknowledged_at` column and touches US1/US3's flows.
- **Polish (Phase 9)** depends on all stories.

Within a phase, `[P]` tasks touch different files; unmarked tasks are sequential.

## Implementation Strategy

**Land the `load_persona` fix first** (T005–T006). It repairs a mechanism that raises today, is one file, and unblocks US5 entirely.

**MVP = US1 + US2** (Phases 1–4). This is the feature: setup becomes a confirmation, and the confirmation is honest. US1 alone is demoable; US2 is what makes reading-instead-of-asking safe rather than merely convenient (spec.md US2 rationale).

**Then**: US5 (voice — high visible value, mostly independent), US3 (goal change — the common real-use case), US4 (reset — the safety counterpart to US3), US6 (disclaimer gate — small).

**T024 is fenced**: the write-back probe needs its own explicit authorisation and can be deferred to FR-007 without blocking anything. The commit message records which way it went.
