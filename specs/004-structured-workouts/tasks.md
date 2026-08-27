---

description: "Task list for 004-structured-workouts"
---

# Tasks: Structured Workouts and Session Library

**Input**: Design documents from `/specs/004-structured-workouts/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/session-library.md](./contracts/session-library.md),
[quickstart.md](./quickstart.md)

**Tests**: **Required, not optional.** FR-014c mandates that changes to plan generation ship with tests
covering the generation path, and Constitution Principle V requires the same for any `app/engine/` change.
Every phase below therefore carries its own test tasks.

**Organization**: Grouped by user story. The build order below deliberately departs from strict priority
order in one place, explained in §Dependencies.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelisable — different files, no dependency on incomplete work
- **[Story]**: US1–US6 per spec.md. Setup, Foundational and Polish carry no story label

## Path Conventions

Single Python project, existing layout. Engine code in `app/engine/`, tests in `tests/test_engine/`,
library content in a new top-level `sessions/` directory (mirroring the existing `personas/`).

---

## Phase 1: Setup — capture the "before" while it still exists

**Purpose**: User Story 2 is a claim that nothing changed, and SC-004 demands byte-identical matching and
adherence results. That is unverifiable unless the baseline is recorded **before** `SessionSpec` is
touched. These tasks are ordered first for that reason, not as ceremony.

- [X] T001 Create `scripts/snapshot_session_behaviour.py` — loads the athlete's real plan and activity corpus from `data/banister.db`, runs activity↔session matching and adherence scoring over every activity, and writes a deterministically ordered JSON document of the results. Exercises `evaluate_activity_plan_match` (mirroring the real ingestion-time `used_slots` computation in `activity_feedback.py`) for every real log regardless of status, plus `compute_session_kpi` for any log that is `"done"` and matched
- [X] T002 Captured the pre-change baseline to `specs/004-structured-workouts/baseline/behaviour.json` (5 entries — the real corpus's 5 logs, all `"unplanned"`, none matched) and recorded counts in `baseline/README.md`: **237 passed, 1 failed, 13 skipped**; ruff 226 violations. The 1 failure (`test_sessions_on_available_days_only`) is a pre-existing, date-triggered flake unrelated to this feature — documented in `baseline/README.md` rather than fixed here or silently excluded, so the T054 gate is evaluated against the real baseline
- [X] T003 [P] Extracted a real pre-change plan document from `training_plans.plan_technical` into `tests/fixtures/plans/legacy_plan.json` (4 weeks, confirmed no session carries `steps`) — taken from the database, never hand-written
- [X] T004 [P] Added `sessions/README.md` documenting the library file format for contributors, derived from [contracts/session-library.md](./contracts/session-library.md)

**Checkpoint**: The "before" is on disk. Nothing in `app/` has changed yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The step model and the derived-summary machinery. Every user story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 Add `Step` model to `app/engine/schemas.py` — `kind` (`warmup|work|recovery|cooldown|steady`), `duration_minutes` (`> 0`), `zone_code`; no absolute watts or bpm, ever (FR-002, FR-022)
- [ ] T006 Add `RepeatGroup` model to `app/engine/schemas.py` — `repeat` (`>= 2`, so one structure has one representation) and a non-empty `steps` list; groups do not nest (FR-003)
- [ ] T007 Add optional `steps: list[Step | RepeatGroup] | None = None` to `SessionSpec` in `app/engine/schemas.py`, leaving every existing summary attribute in place with unchanged meaning (FR-008, FR-013)
- [ ] T008 Implement summary derivation helpers in `app/engine/schemas.py` — `duration_minutes`, `zone_code`, `target_time_in_zone_minutes` and `tss_target` from steps, per the derivation table in [data-model.md](./data-model.md); reuse `app/engine/tss.py`'s existing per-zone factors so a structured session and today's flat estimate agree in method (FR-005, FR-006, FR-009)
- [ ] T009 Add a `model_validator` to `SessionSpec` making steps↔summary disagreement a load-time error **when steps are present**, and a no-op when they are absent (FR-009 reconciled with FR-013 — see data-model.md §"Why the summary stays stored")
- [ ] T010 [P] Write `tests/test_engine/test_structured_sessions.py` covering the schema layer: duration equals the sum of steps asserted against **independently stated totals** (e.g. threshold 3×12r4 → `15 + 36 + 8 + 15 = 74`), never against the derivation itself; `repeat: 1` rejected; a repeat group's contribution counted `repeat ×`
- [ ] T011 [P] Extend `tests/test_engine/test_structured_sessions.py` with the legacy path: `tests/fixtures/plans/legacy_plan.json` validates, keeps its stored summary verbatim, and reports step absence rather than fabricating steps (FR-013, FR-014)

**Checkpoint**: A session can carry steps and the summary provably cannot drift from them. Nothing
generates steps yet, so behaviour is unchanged — run `scripts/snapshot_session_behaviour.py` and confirm
it still matches T002's baseline.

---

## Phase 3: User Story 1 — A session says what to actually do (P1) 🎯 MVP

**Goal**: Every generated session carries an ordered sequence of steps whose durations and intensities are
individually stated.

**Independent Test**: Generate a plan; confirm every session — including endurance and recovery rides —
presents ordered steps, and that stated duration equals the sum of steps in 100% of them.

**Why this is small**: `plan_builder.py` already computes the structure and discards it (research R1).
This phase stops the discarding; it does not invent a structural model.

- [ ] T012 [US1] Emit steps for interval sessions in `app/engine/plan_builder.py` — build `[warmup, RepeatGroup(sets × [work, recovery]), cooldown]` from the existing `SWEET_SPOT_STRUCTURES`/`THRESHOLD_STRUCTURES`/`VO2_STRUCTURES` tuples and `WARMUP_MIN`/`WARMUP_VO2_MIN`/`COOLDOWN_MIN` constants, preserving the exact structures currently prescribed
- [ ] T013 [US1] Emit a single `steady` step for `long_ride`, `endurance` and `recovery` sessions in `app/engine/plan_builder.py`, so an unstructured session is the same kind of thing as a structured one rather than a special case (FR-004)
- [ ] T014 [US1] Emit steps for the three race-week sessions in `app/engine/plan_builder.py::_build_race_week()` (FR-001 applies to every generated session, and race week is currently a separate construction path)
- [ ] T015 [US1] Replace `_structure_duration()`'s silent `max(40, min(120, …))` clamp in `app/engine/plan_builder.py` with derivation from steps, so duration can no longer disagree with its own parts (FR-005; the clamp becomes a fitting constraint in T031, not an output-mangler — research R2)
- [ ] T016 [US1] Write `tests/test_engine/test_plan_builder_steps.py` asserting every session of a freshly generated plan carries steps, that repeated efforts appear as a `RepeatGroup` rather than duplicated steps, and that SC-002 holds across the whole plan
- [ ] T017 [US1] Verify existing `tests/test_engine/test_plan_builder.py` still passes unchanged — its assertions are the behavioural baseline for what this phase must not move

**Checkpoint**: Sessions describe themselves. `/plan` in Telegram shows the same content as before (steps
are carried but not yet rendered), and the T002 baseline snapshot still matches.

---

## Phase 4: User Story 2 — Everything that already reads sessions keeps working (P1)

**Goal**: Plan display, reminders, matching, adherence, conversational context and plan modification all
behave exactly as before.

**Independent Test**: `diff baseline/behaviour.json after.json` is empty, and every reader is exercised.

**Where the risk actually is**: 7 construction sites in `plan_modifier.py`, which is LLM-driven and so not
fully predictable from tests (research R7).

- [ ] T018 [US2] Make all 7 `SessionSpec` construction sites in `app/engine/plan_modifier.py` emit steps consistent with their summary — injury adaptation (`adapt_plan_for_injury`), week adjustment (`apply_proposed_modification`), session adjustment (`apply_session_adjustment`, 2 sites), day shifting, and `_apply_progressive_recovery` (FR-012)
- [ ] T019 [US2] Handle zone downgrade and duration scaling in `app/engine/plan_modifier.py::_downgrade_zone()`/`_downgrade_duration_factor()` so a downgraded session's steps are rewritten rather than left describing the pre-downgrade session
- [ ] T020 [US2] Ensure a modification applied to a **legacy** session (no steps) leaves it legacy rather than fabricating steps for it (FR-014) in `app/engine/plan_modifier.py`
- [ ] T021 [US2] Write `tests/test_engine/test_plan_modifier_steps.py` asserting every modification path leaves the session internally consistent, and that modifying a legacy session does not invent structure
- [ ] T022 [US2] Re-run `scripts/snapshot_session_behaviour.py` and confirm `diff` against `specs/004-structured-workouts/baseline/behaviour.json` is **empty** — byte-identical matching and adherence (SC-004, FR-010, FR-011)
- [ ] T023 [US2] Run `uv run python -m eval.runner` and confirm the offline plan-quality harness still executes against step-carrying plans (FR-014a) — it calls `generate_plan()` directly, so generation changes can break it
- [ ] T024 [US2] Compare the eval result against a pre-change run and confirm no quality regression, recording both runs under `eval/results/` (FR-014b, SC-005a)
- [ ] T025 [US2] Verify the daily reminder (`app/main.py::_format_reminder()`) and the weekly review (`app/services/weekly_recap.py`) produce correct output against step-carrying sessions, and that a rest day is still silence rather than an empty session (SC-005b — called out separately in the spec because neither is covered by the corpus diff)
- [ ] T026 [US2] Exercise a conversational plan modification end to end through the coach in Telegram, by hand — the one reader whose output tests cannot fully predict (FR-012)

**Checkpoint**: The compatibility claim is evidenced, not asserted. US1 + US2 together are a complete,
shippable increment.

---

## Phase 5: User Story 5 — Plans created before the change still open (P2)

**Goal**: A plan generated by the previous version still displays, matches and scores.

**Independent Test**: Open `tests/fixtures/plans/legacy_plan.json` and exercise every feature that reads it.

**Sequenced here, ahead of US3/US4**, because the legacy path is a property of the schema built in Phase 2
and of the readers touched in Phase 4 — verifying it now closes the P1 increment rather than leaving a
known-unverified path open while the library is built.

- [ ] T027 [US5] Verify a legacy plan loads, displays via `/plan` and `/week N`, matches activities and scores adherence, using `tests/fixtures/plans/legacy_plan.json` (SC-005)
- [ ] T028 [US5] Verify a plan holding **both** legacy and structured sessions behaves correctly — the ordinary state during a transition, requiring no handling beyond per-session nullability
- [ ] T029 [US5] Verify generating a new plan leaves the stored legacy plan unaffected

**Checkpoint**: P1 increment complete and safe to leave in place. The remaining phases add capability
without changing what the athlete already has.

---

## Phase 6: User Story 3 — Sessions come from a library (P2)

**Goal**: The generator selects from an editable library instead of constructing sessions inline.

**Independent Test**: Add a template to `sessions/` and confirm the generator can select it with no
change to any `.py` file.

- [ ] T030 [US3] Implement `app/engine/session_library.py` — load and validate `sessions/*.yaml` per [contracts/session-library.md](./contracts/session-library.md), failing at load time with the file, template `id` and broken rule named; reject duplicate ids; import nothing from `bot/` or `llm/` (Constitution Principle III)
- [ ] T031 [US3] Implement template selection in `app/engine/session_library.py` — indexed by `(phase, workout_type, family)`, candidates sorted by `id` then rotated by `week_in_block` to preserve today's variety while being repeatable regardless of filesystem order (FR-020); raise and name the unsatisfied request when nothing matches (FR-021)
- [ ] T032 [P] [US3] Author `sessions/threshold.yaml`, `sessions/sweet-spot.yaml` and `sessions/vo2.yaml` from the existing `plan_builder.py` structures, each carrying `purpose`, `intent` and `suits` (FR-017)
- [ ] T033 [P] [US3] Author `sessions/endurance.yaml`, `sessions/long-ride.yaml`, `sessions/recovery.yaml` and `sessions/race-week.yaml` with the same rationale fields
- [ ] T034 [US3] Switch `app/engine/plan_builder.py::_build_sessions()` and `_build_race_week()` to select from the library, deleting the now-redundant `SWEET_SPOT_STRUCTURES`/`THRESHOLD_STRUCTURES`/`VO2_STRUCTURES` constants and `_get_sweet_spot()`/`_get_threshold()`/`_get_vo2()` (FR-018)
- [ ] T035 [US3] Write `tests/test_engine/test_session_library.py` asserting the library loads, that every `(phase, workout_type)` the periodization can request has at least one template (FR-019, SC-007), and that selection is deterministic across repeated runs
- [ ] T036 [US3] Verify the coverage test **fails** when a template is deliberately removed — a coverage assertion that cannot fail proves nothing (quickstart Scenario 3)
- [ ] T037 [US3] Verify a new template added to `sessions/` becomes selectable with zero `.py` changes (FR-016, SC-006), and that the load-error cases are legible: duplicate `id`, `repeat: 1`, and an undefined zone code
- [ ] T038 [US3] Re-run the behaviour snapshot and the eval harness after the library switch — moving construction into data is the change most likely to alter generated plans (FR-010, FR-011, SC-005a)

**Checkpoint**: A training-literate contributor can add a session without reading Python.

---

## Phase 7: User Story 4 — Intensities follow the athlete (P2)

**Goal**: Templates fit the athlete's targets and thresholds; changing a threshold retargets future
sessions and touches no completed record.

**Independent Test**: Change the athlete's FTP, regenerate, confirm future targets move and
`session_logs` is unchanged.

- [ ] T039 [US4] Implement `app/engine/fitting.py` — adapt a template to a session's load share within its `ScalingRules` bounds, preserving structural character (FR-023); return a `SessionSpec` with steps
- [ ] T040 [US4] Implement fitting refusals in `app/engine/fitting.py` — refuse and name the failed constraint when the load target cannot be met within bounds, when the fitted session exceeds the athlete's stated availability, or when work intervals fall below a meaningful floor (FR-024); never silently clamp, which is the behaviour T015 removed
- [ ] T041 [US4] Add `ScalingRules` to the template model in `app/engine/session_library.py` and populate `scaling` in the `sessions/*.yaml` files authored in T032/T033, so elasticity is per-template rather than global (FR-016)
- [ ] T042 [US4] Resolve step zone codes to absolute targets at presentation time against `TrainingPlanSchema.zones`, in power or heart-rate terms per `coaching_mode` (FR-026) — no new zone machinery needed, `Zone` already carries both (research R5)
- [ ] T043 [US4] Present relative zones with absolutes simply absent when the athlete has no threshold, never fabricated (FR-027, Constitution Principle IV)
- [ ] T044 [US4] Write `tests/test_engine/test_fitting.py` covering structural character preservation, every refusal case, heart-rate mode, and the no-threshold case
- [ ] T045 [US4] Verify that changing the athlete's FTP retargets every future session and leaves every `session_logs` record unchanged (SC-008) — true by construction since steps store zone codes only, but asserted anyway, because "by construction" is what stops being true after a refactor
- [ ] T046 [US4] Re-run the eval harness and settle the open question the plan deliberately left open: what "preserving structural character" means numerically, decided by the before/after comparison rather than argued in advance (research §Open questions)

**Checkpoint**: Sessions are correct for this athlete, and wrong inputs are refused rather than silently
mangled.

---

## Phase 8: User Story 6 — Descriptions are not locked to one language (P3)

**Goal**: A session description is produced from its structure, in a chosen language, rather than
retrieved from French text embedded in the generator.

**Independent Test**: Render the same session in two languages and confirm both follow the structure.

**Scope boundary**: this phase delivers the renderer and its contract. End-to-end switching driven by the
configured persona (FR-029) completes in spec 007, which owns coach voice — `load_persona()` exists but is
called from nowhere, and both specs name that wiring as a shared prerequisite (research R6).

- [ ] T047 [US6] Implement `app/engine/session_render.py` — produce a description from a session's steps, taking a language parameter (FR-028)
- [ ] T048 [US6] Stop constructing French sentences in `app/engine/plan_builder.py::_session_description()`, sourcing descriptions from the renderer instead; retain `SessionSpec.description_fr` as a populated compatibility field so the ~20 existing readers are untouched (FR-008, FR-030)
- [ ] T049 [US6] Preserve the Z5/Z6 heart-rate RPE caveat currently appended by `_session_description()` as a renderer rule rather than losing it — it is real coaching content, not boilerplate
- [ ] T050 [US6] Write `tests/test_engine/test_session_render.py` asserting descriptions derive from structure, follow the language parameter, and that `grep -rn "description_fr" app/engine/plan_builder.py` shows no hardcoded French sentence construction remaining (SC-010)

**Note**: `Zone.description_fr` in `app/engine/zones.py` is deliberately **out of scope** — it is
plan-level rather than session-level, and touching it pulls in the persona wiring spec 007 owns.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T051 Run every scenario in [quickstart.md](./quickstart.md) and record the outcome, including the scenarios that must fail when sabotaged (T036) rather than only the happy paths
- [ ] T052 [P] Update `CLAUDE.md` — the `SessionSpec` schema block, the architecture tree (`sessions/`, three new engine modules), and the navigation table, per the project's documentation policy
- [ ] T053 [P] Update `README.md` project structure to include `sessions/`
- [ ] T054 Confirm the full suite is green and no new lint violations beyond the 226 baseline recorded in T002
- [ ] T055 If any Section 11 material was copied verbatim at any point, ship a `NOTICE` carrying its MIT licence and attribution in the same change (spec 001 FR-028) — the plan avoids this by building from the project's own structures, so this task is a **precondition check**, not an assumed deliverable
- [ ] T056 Delete `specs/004-structured-workouts/baseline/` once SC-004 has been demonstrated, or keep it deliberately and say why — a stale baseline that silently stops being the real "before" is worse than none

---

## Dependencies & Execution Order

### Phase dependencies

```
Phase 1 (Setup — capture the before)
   └─> Phase 2 (Foundational: Step, RepeatGroup, derivation, validator)   ⟵ blocks everything
          ├─> Phase 3 (US1: generator emits steps)              🎯 MVP
          │      └─> Phase 4 (US2: readers + modifier unbroken)
          │             └─> Phase 5 (US5: legacy plans verified)   ⟵ closes the P1 increment
          │                    ├─> Phase 6 (US3: library)
          │                    │      └─> Phase 7 (US4: fitting + relative intensity)
          │                    └─> Phase 8 (US6: rendering)  ⟵ independent of 6 and 7
          └─> Phase 9 (Polish)
```

### The one deliberate departure from priority order

US5 (P2) is sequenced **before** US3 and US4 (both P2). Priority ranks value; this ordering follows risk.
US5 verifies a property of the schema built in Phase 2 and the readers touched in Phase 4, so verifying it
immediately closes a complete, safe increment. Deferring it would leave the athlete's stored plan on an
unverified path for the whole duration of the library work.

### Within phases

- Phase 1: T003 and T004 are `[P]` — different files, no interdependency
- Phase 2: T010 and T011 are `[P]` — both add to the same new test file but cover disjoint paths; if that file is written by one person, drop the `[P]`
- Phase 6: T032 and T033 are `[P]` — separate YAML files
- Phase 9: T052 and T053 are `[P]` — different documents

### The gate that matters

**T002 must complete before any change to `app/engine/schemas.py`.** SC-004 requires byte-identical
matching and adherence results, and there is no way to demonstrate that after the fact. If the baseline is
not captured first, US2's central claim becomes untestable and the safest available evidence is lost
permanently.

---

## Story → phase mapping

| Story | Priority | Delivered by | Verified by |
|---|---|---|---|
| US1 — A session says what to do | P1 | Phase 3 | T016, T017; quickstart 1 |
| US2 — Existing readers keep working | P1 | Phase 4 | **T022** (byte-identical), T025, T026; quickstart 2 |
| US5 — Legacy plans still open | P2 | Phase 5 | T027–T029; quickstart 5 |
| US3 — Sessions come from a library | P2 | Phase 6 | T035, **T036**, T037; quickstart 3 |
| US4 — Intensities follow the athlete | P2 | Phase 7 | T044, T045; quickstart 4 |
| US6 — Descriptions not language-locked | P3 | Phase 8 | T050; quickstart 8 |

---

## Implementation Strategy

### MVP (Phases 1–5)

Phases 1 through 5 deliver the whole point of the feature — sessions that say what to do — while proving
nothing else moved and the athlete's existing plan still works. That is a complete, shippable increment,
and it is where the compatibility risk lives. Stop and validate here before starting the library.

### Safe stopping points

Every phase from 3 onward leaves the system working. Phases 6–8 add capability without changing what the
athlete already has: the library changes *where sessions come from*, not what they are, and rendering
changes only presentation. The one irreversible-feeling moment is T034 (deleting the inline structures),
which is safe precisely because T038 re-runs the snapshot and the eval harness immediately after.

### What to be suspicious of

- **T022 passing on the first attempt.** The derivation reimplements arithmetic that previously lived in
  several places; an empty diff on the first run is more likely to mean the snapshot is not capturing what
  it should than that the reimplementation is perfect. Verify the snapshot fails when deliberately
  sabotaged, the same way T036 verifies the coverage test.
- **T010 asserting the derivation against itself.** Stated totals must be written out by hand.
- **Fabricated steps for legacy sessions.** A plausible warm-up invented for a session whose structure was
  discarded would look correct everywhere and be wrong — exactly what FR-014 and Constitution Principle IV
  forbid.
