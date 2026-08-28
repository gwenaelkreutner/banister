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

- [X] T005 Added `Step` model to `app/engine/schemas.py` — `kind` (`warmup|work|recovery|cooldown|steady`), `duration_minutes` (`> 0`), `zone_code`; no absolute watts or bpm, ever (FR-002, FR-022)
- [X] T006 Added `RepeatGroup` model to `app/engine/schemas.py` — `repeat` (`>= 2`) and a non-empty `steps` list; groups do not nest (FR-003)
- [X] T007 Added optional `steps: list[Step | RepeatGroup] | None = None` to `SessionSpec`, every existing summary attribute unchanged (FR-008, FR-013)
- [X] T008 Implemented `derive_duration_minutes`/`derive_zone_code`/`derive_target_time_in_zone_minutes` in `app/engine/schemas.py`, and `estimate_structured_session_tss()` in `app/engine/tss.py` (FR-005, FR-006, FR-009). **Design refinement recorded, not silently made**: `tss_target` derivation needs `coaching_mode`, which `SessionSpec` does not carry (it's plan-level) — so it is *not* part of the Pydantic `model_validator` (T009); it's enforced by construction-time callers (Phase 3+) instead. **Also found and fixed while implementing**: the old `sets*work + (sets-1)*rest` duration formula dropped the recovery after the final repetition; the `RepeatGroup` model doesn't special-case that, so a repeated unit's total moves slightly upward — flagged for the eval-harness quality check in Phase 4/7 rather than assumed harmless (updated `quickstart.md`'s worked example from 74 to 78 minutes accordingly)
- [X] T009 Added a `model_validator(mode="after")` to `SessionSpec` — duration/zone/time-in-zone disagreement with steps is a load-time `ValidationError`, a no-op when steps are absent
- [X] T010 [P] Wrote `tests/test_engine/test_structured_sessions.py` (16 tests) — duration asserted against an independently hand-computed total (`15 + 3×(12+4) + 15 = 78`, not against the derivation itself), `repeat: 1` rejected, zero-length step rejected, each of the three validator disagreement cases rejected individually
- [X] T011 [P] Same file: `tests/fixtures/plans/legacy_plan.json` loads, every session confirmed `steps is None`, a mixed legacy+structured plan loads correctly (US5 edge case)

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

- [X] T012 [US1] Emitted steps for interval sessions via new `_build_interval_steps()` — `[warmup, RepeatGroup(sets × [work, recovery]), cooldown]`, from the existing `SWEET_SPOT_STRUCTURES`/`THRESHOLD_STRUCTURES`/`VO2_STRUCTURES` tuples. `_interval_tss()` rewritten to compute from these same steps (`estimate_structured_session_tss()`) instead of an ad hoc sum, guaranteeing FR-006 agreement. Also handled two special-case sessions that don't come from the three structure lists: the taper phase's "3×8min activation" (new `_build_activation_steps()`) and the recovery week's Z6 sprint-activation day (steps at 1-minute granularity — the coarsest `Step` supports — as the closest structural representation of a 30-second sprint; the description keeps the real prescribed duration, the structure is a deliberately approximate summary of it)
- [X] T013 [US1] Emitted a single `steady` step (new `_build_steady_steps()`) for `long_ride`, `endurance` and `recovery` sessions — including the recovery-week's Z1/Z2 sessions and the post-hoc volume-rescaling block, which rebuilds `SessionSpec`s and now rebuilds their step too so it can't disagree with the rescaled duration (FR-004)
- [X] T014 [US1] Emitted steps for all three race-week sessions in `_build_race_week()`: the Z2 endurance session and the race-day marker are `_build_steady_steps()`; the Z5 activation session gets custom inline steps (its warmup is Z2, not Z1, so it doesn't fit `_build_interval_steps()`'s shape)
- [X] T015 [US1] Replaced `_structure_duration()`'s clamp with pure derivation (`warmup_min + sets*(work+rest) + COOLDOWN_MIN`) — confirmed no existing structure exceeds the old 120min ceiling even with the trailing recovery now included, so removing the clamp changes no behaviour beyond the totals already flagged in Phase 2. Also deleted `_target_time_in_zone_minutes()` entirely: superseded by `derive_target_time_in_zone_minutes()` on the actual constructed steps, and confirmed dead (its one caller was the line it used to feed)
- [X] T016 [US1] Wrote `tests/test_engine/test_plan_builder_steps.py` (7 tests) — every session in a freshly generated plan (power mode, HR mode, and a plan with a recovery week) carries steps; duration equals `derive_duration_minutes(steps)` for every session; steady sessions are exactly one step; at least one interval session uses a `RepeatGroup`; no interval session represents repeated work as duplicated flat `Step` entries
- [X] T017 [US1] Verified `tests/test_engine/test_plan_builder.py` — 11/12 pass unchanged, including `test_taper_activation_interval_targets_24_minutes_in_zone_when_present`, which independently confirms the new derivation reproduces the old hardcoded target (24 = 3×8) exactly. The 12th (`test_sessions_on_available_days_only`) is the pre-existing, date-triggered flake documented in `baseline/README.md` — unaffected by this phase, still fails identically with and without these changes (verified via `git stash`)

**Verified beyond the task list**: `eval/runner` re-run before and after this phase (`git stash`) produces the *same* single deterministic warning (`recovery_week_too_high`, pre-existing) — confirming this phase introduced no new eval-detected regression. The behaviour snapshot (`scripts/snapshot_session_behaviour.py`) remains byte-identical to the Phase 1 baseline, as expected — the stored plan the real corpus matches against wasn't regenerated.

**Checkpoint**: Sessions describe themselves. `/plan` in Telegram shows the same content as before (steps
are carried but not yet rendered), and the T002 baseline snapshot still matches.

---

## Phase 4: User Story 2 — Everything that already reads sessions keeps working (P1)

**Goal**: Plan display, reminders, matching, adherence, conversational context and plan modification all
behave exactly as before.

**Independent Test**: `diff baseline/behaviour.json after.json` is empty, and every reader is exercised.

**Where the risk actually is**: 7 construction sites in `plan_modifier.py`, which is LLM-driven and so not
fully predictable from tests (research R7).

- [X] T018 [US2] All 6 real `SessionSpec` construction sites in `app/engine/plan_modifier.py` now emit steps consistent with their summary (research R7 said "~7"; the exact count, confirmed by grep, is 6): injury adaptation, week adjustment's **propose and apply**, session adjustment's reduce_50/indoor/shift, and progressive recovery. New `_scale_steps()` helper scales every step's duration (min 1 minute) and remaps zone codes. **Design finding**: `propose_week_adjustment`/`apply_proposed_modification` communicate via a plain JSON dict shown to the user for confirmation — the dict genuinely had no step information, so threading structure through required serializing it into the proposal (`_steps_to_json`/`_steps_from_json`) rather than just reusing the in-memory object, unlike the other 5 sites, which already hold the original `SessionSpec` directly and could scale its `.steps` in place
- [X] T019 [US2] Zone downgrade/duration scaling handled via `_scale_steps(steps, effective_factor, zone_remap)` at every site that downgrades a zone — `zone_remap` only touches the dominant zone actually being restricted, leaving warmup/cooldown/recovery `Z1` steps untouched unless `Z1` itself is restricted
- [X] T020 [US2] `_scale_steps(None, ...)` returns `None` — a legacy session passed through any modification path stays legacy, verified explicitly by two tests (not just the general assertion)
- [X] T021 [US2] Wrote `tests/test_engine/test_plan_modifier_steps.py` (9 tests) against a **real** `plan_builder.py`-generated plan (not hand-written) — covers all 6 sites plus the two legacy-stays-legacy cases. No prior test file existed for `plan_modifier.py` at all (a pre-existing gap this phase's scope doesn't extend to closing in full)
- [X] T022 [US2] `scripts/snapshot_session_behaviour.py` re-run — `diff` against the Phase 1 baseline is **empty** (SC-004, FR-010, FR-011)
- [X] T023 [US2] `eval.runner` re-run — still executes successfully against step-carrying plans (FR-014a)
- [X] T024 [US2] Compared against the pre-change run captured during Phase 3 (`git stash`) — **same single warning both times** (`recovery_week_too_high`, pre-existing per T012's note), confirming no additional regression accumulated in Phase 4 (FR-014b, SC-005a)
- [X] T025 [US2] Verified by direct call: `_format_reminder()` only reads `.workout_type`/`.zone_code`/`.duration_minutes`/`.target_time_in_zone_minutes` — unaffected by `steps`, confirmed against a real step-carrying session (no crash; the one error seen was a Windows terminal's cp1252 encoding of the 🚴 emoji at *print* time, after the function had already returned). `tests/test_services/test_weekly_recap.py` (4 tests) passes unchanged (SC-005b)
- [X] T026 [US2] Exercised through real Telegram — "je suis fatigué cette semaine, tu peux alléger ?" produced a real `propose_plan_modification` call, a correct proposal (Saturday 198min Z3 → 137min Z2, Sunday's long ride → Z1 recovery), and confirmed structured `steps` threaded through the proposal dict (FR-012). **Found and fixed three real, pre-existing bugs along the way**, none introduced by this spec but all blocking this exact test until fixed:
  1. `/setup` never deactivated the athlete's previous plan (`app/db/repositories/plan_repo.py` had no such function despite a comment claiming it existed) — a second `/setup` left two `status="active"` plans, and `get_active_plan()`'s `scalar_one_or_none()` raised `MultipleResultsFound` on every subsequent read (`/plan`, `/forme`, `/recap`, the chat), silently. Fixed with `deactivate_all_for_user()`, called before every plan creation; the real database (which had exactly this two-active-plans state) backed up and repaired
  2. `zoneinfo` has no bundled IANA database on Windows (and may not on a slim Docker base image either) — `ZoneInfo("Europe/Paris")` in `app/llm/tools.py` raised `ZoneInfoNotFoundError` on every conversational message, caught by chat.py's generic handler and shown as "problème technique". Fixed by adding `tzdata` as a declared dependency
  3. No message handler existed for `PlanStates.PENDING_MODIFICATION` — only the ✅/❌ button callbacks. A free-text message sent while a proposal was pending matched no handler and was silently dropped by aiogram (reproduced live: "décale ma séance de samedi" while the fatigue proposal was still pending got no response at all). Fixed with a handler that tells the athlete to confirm or cancel first

**Checkpoint**: The compatibility claim is evidenced, not asserted. US1 + US2 together are a complete,
shippable increment.

---

## Phase 5: User Story 5 — Plans created before the change still open (P2)

**Goal**: A plan generated by the previous version still displays, matches and scores.

**Independent Test**: Open `tests/fixtures/plans/legacy_plan.json` and exercise every feature that reads it.

**Sequenced here, ahead of US3/US4**, because the legacy path is a property of the schema built in Phase 2
and of the readers touched in Phase 4 — verifying it now closes the P1 increment rather than leaving a
known-unverified path open while the library is built.

- [X] T027 [US5] Wrote `tests/test_engine/test_legacy_plan_compat.py` — `_format_week()` (the function behind `/plan`/`/week N`) renders every week of the real legacy fixture without error; `score_activity_vs_session()` and `compute_session_kpi()` score a legacy session without error (SC-005). This is also the third independent confirmation of the same guarantee: `scripts/snapshot_session_behaviour.py` has exercised matching+adherence against the real DB's own legacy-shaped plan on every phase of this feature and stayed byte-identical throughout — this test makes that check part of `pytest` rather than something a developer has to remember to run separately
- [X] T028 [US5] Same file: a plan with one structured session grafted onto an otherwise-legacy fixture renders and scores correctly through the same code path, no special-casing
- [X] T029 [US5] Same file: generating an independent new plan (`generate_plan()`, which never reads or writes any existing plan document) leaves the legacy fixture file byte-identical on disk, verified by re-reading and comparing, not just asserted

**Checkpoint**: P1 increment complete and safe to leave in place. The remaining phases add capability
without changing what the athlete already has.

---

## Phase 6: User Story 3 — Sessions come from a library (P2)

**Goal**: The generator selects from an editable library instead of constructing sessions inline.

**Independent Test**: Add a template to `sessions/` and confirm the generator can select it with no
change to any `.py` file.

- [X] T030 [US3] Implemented `app/engine/session_library.py` — loads/validates `sessions/*.yaml`, every load error names the file, template id, and broken rule; duplicate ids rejected; imports nothing from `bot/`/`llm/` (Constitution Principle III)
- [X] T031 [US3] `select_template(phase, workout_type, week_in_block, family)` — candidates sorted by id then rotated by `week_in_block`; raises `SessionLibraryError` naming the unsatisfied request when nothing matches (FR-021)
- [X] T032 [P] [US3] Authored `sessions/threshold.yaml` (4 templates + `taper-activation`), `sessions/sweet-spot.yaml` (3), `sessions/vo2.yaml` (3) — all with `purpose`/`intent`/`suits` (FR-017). **Design finding**: `threshold`'s zone is chosen dynamically (Z3 early-build, Z4 later — `_build_week_template`'s `phase_progress` gate) — a template can't express that, so the template fixes Z4 as canonical and `plan_builder.py` remaps to Z3 when needed, the same pattern as the existing injury zone-remap
- [X] T033 [P] [US3] Authored `sessions/endurance.yaml`, `sessions/long-ride.yaml`, `sessions/recovery.yaml` (+ `recovery-sprint-activation`), `sessions/race-week.yaml` (3 templates) — steady templates carry a placeholder duration; the real size still comes from `plan_builder.py`'s volume/TSS-budget logic, matching plan.md's "library controls shape, periodization controls size" boundary
- [X] T034 [US3] `_build_sessions()` and `_build_race_week()` now select from the library — `SWEET_SPOT_STRUCTURES`/`THRESHOLD_STRUCTURES`/`VO2_STRUCTURES` and the old `_get_sweet_spot()`/`_get_threshold()`/`_get_vo2()` deleted, replaced by library-backed versions with the **same return signature** (`(duration, label, sets, work, rest)`) — deliberately, to avoid touching `_assign_sessions_to_days()`'s delicate physiological-placement logic (gap constraints, high-intensity spacing) at all. **Bug found and fixed before it shipped**: `_build_interval_steps()`'s cooldown was hardcoded to `COOLDOWN_MIN` (15), but the `taper-activation` template declares 10 — would have silently disagreed with the template it claims to materialize; added a `cooldown_min` parameter
- [X] T035 [US3] Wrote `tests/test_engine/test_session_library.py` (26 tests) — load, all 16 required `(phase, workout_type)` coverage cases individually parametrized, deterministic selection, rotation variety, legible load errors (duplicate id, `repeat: 1`, invalid `workout_type`)
- [X] T036 [US3] The coverage-can-fail test caught its own first draft: a plain `(phase, workout_type)` filter also matched `recovery.yaml`'s `sprint_activation` template even with `sweet-spot.yaml` deleted, passing when it shouldn't have — exactly the trap this task warns about. Fixed by scoping the sabotage-detection test to `family="sweet_spot"` specifically
- [X] T037 [US3] Verified in an isolated temp copy of the real library (never touching `sessions/` itself): a new template becomes selectable with zero `.py` changes; duplicate id, `repeat: 1`, and invalid `workout_type` all fail at load time with legible messages
- [X] T038 [US3] Behaviour snapshot re-run — byte-identical. **Found and fixed a real snapshot-script bug while doing this**: it assumed every `session_log` belongs to the currently *active* plan, which broke (0 entries instead of 5) after the athlete regenerated their plan for real during T026 — the 5 real logs still pointed at the now-inactive plan. Fixed by resolving each log against its own `plan_id` rather than the active plan, which is also the more correct design (a log shouldn't become unscoreable just because a later `/setup` superseded its plan). `eval.runner` re-run — same single pre-existing warning as Phase 3/4, no new regression from moving construction into data

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
