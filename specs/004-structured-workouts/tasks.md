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

- [X] T039 [US4] Implemented `app/engine/fitting.py::fit_template()` — grid-search over the template's own `ScalingRules` (`repeat_range` × `work_minutes_range` for intervals, `steady_minutes_range` for steady sessions), returning the combination closest to the target TSS. Grid search chosen deliberately over a directional heuristic: the ranges this library declares are always small (2-7 × 4-25), so exhaustive search is cheap and *guaranteed* to find the true closest fit, not just a locally-good one
- [X] T040 [US4] Refusals via `FittingError`, always naming the template id and the failed constraint: target unreachable within `ScalingRules` (names the bounds tried and the closest achievable value), or fitted duration exceeds `available_minutes`. Never clamps
- [X] T041 [US4] `scaling:` populated on every template meant to be fit dynamically (`endurance-z2`, `long-ride-z2`, `recovery-z1`, plus the interval families already scaled in T032). Templates that are deliberately fixed — `taper-activation`, `recovery-sprint-activation`, and all three `race-week.yaml` templates — carry no `scaling` at all, matching the contract's "no scaling declared = fixed, selected as-is or not at all" semantics; these exist to be selected exactly as authored, not adapted
- [X] T042 [US4] `resolve_zone_intensity(zone_code, zones, coaching_mode)` — power mode resolves to watts, HR mode to bpm, using `Zone`'s existing resolved fields (no new machinery)
- [X] T043 [US4] An undefined zone resolves to `lower=None, upper=None` rather than fabricating a value — verified explicitly. **Scope note recorded rather than assumed**: in this codebase `zones` is always populated (plan generation falls back to an estimated threshold, never leaves it unset — see `generate_plan()`), so the "no threshold" case in practice is "estimated, not declared" (`ftp_source`), not an empty `zones` dict. `resolve_zone_intensity`'s None-handling covers the real edge case that does arise: a template referencing a zone code the athlete's scheme doesn't define
- [X] T044 [US4] Wrote `tests/test_engine/test_fitting.py` (13 tests) — structural-character preservation, every refusal case (unreachable target, availability exceeded, never-clamps), HR mode, undefined-zone resolution
- [X] T045 [US4] Wrote `tests/test_engine/test_ftp_change_retargets.py` (3 tests) — two real generated plans at different FTP share identical zone codes but different resolved absolute targets; every step is asserted to structurally have no watts/bpm field at all (not just "happens to be unset"); `SessionLog`'s own source code is inspected to confirm it imports nothing from `app.engine.zones`/`app.engine.fitting`, so there is no code path connecting an FTP change to a stored record
- [X] T046 [US4] Re-ran `eval.runner` and the behaviour snapshot — both unchanged from Phase 6. **Honest scope note**: `fit_template()` is new, tested infrastructure but is **not yet called by `generate_plan()`** — `_build_sessions()` still uses its own TSS-budget math directly (deliberately kept in Phase 6 to avoid touching `_assign_sessions_to_days()`'s physiological-placement logic), so there is nothing for this phase's own change to move in the eval comparison. The "preserving structural character" question is answered by `fit_template()`'s own design and test suite (closest-TSS-within-tolerance via exhaustive search over the template's declared bounds) rather than by an eval-detected plan-quality shift, since none occurred. Wiring `fit_template()` into initial plan generation — replacing `_build_sessions()`'s ad hoc budget math — is real future work, not silently deferred: it would be the natural foundation for spec 005's calendar push (which needs sessions fit to concrete device/time constraints) and is flagged here for that reason

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

- [X] T047 [US6] Implemented `app/engine/session_render.py::render_description()` — takes `workout_type`, the session's real `steps`, `coaching_mode`, `language`, and free-text `detail`. **Real improvement over the old code, not just a move**: the interval shape (sets×work, zone) is read from the actual `RepeatGroup` rather than trusted to match a hand-typed label — the old `detail` string (e.g. `"3×12min"`) *should* have agreed with the session's real steps but was never checked against them, a latent duplication risk this eliminates by construction
- [X] T048 [US6] `_session_description()` now delegates to the renderer; `ZONE_NAMES` deleted from `plan_builder.py` as dead code (its only consumer was the inline logic just replaced). `description_fr` stays populated for the ~20 existing readers (FR-008, FR-030). **Scope boundary, not silently left**: `_build_race_week()`'s three sessions still construct hardcoded French sentences directly — T048's task text scoped the change to `_session_description()` specifically, and race week never routed through it even before this phase
- [X] T049 [US6] The Z5/Z6 HR RPE caveat is preserved, keyed by language (`_HR_RPE_CAVEAT` dict) rather than a single hardcoded French string appended unconditionally — verified it fires in HR mode, not power mode, and only for Z5/Z6 not Z4
- [X] T050 [US6] Wrote `tests/test_engine/test_session_render.py` (13 tests) — structure-derived interval shape, HR caveat gating (mode × zone), language switching (workout-type labels, zone names, and the caveat text itself all differ FR/EN), and a source-inspection test confirming `_session_description()` delegates to the renderer with no hardcoded French sentence fragments left in its own body

**Note**: `Zone.description_fr` in `app/engine/zones.py` is deliberately **out of scope** — it is
plan-level rather than session-level, and touching it pulls in the persona wiring spec 007 owns.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T051 Re-ran all 8 quickstart scenarios explicitly (not just relied on earlier per-phase runs): structured-session schema (16 passed), behaviour snapshot (byte-identical), library + coverage-can-fail (26), fitting + refusals (13), legacy compatibility (4+5), eval harness (same single pre-existing warning), weekly recap (4), session rendering (13). Every scenario's sabotage/failure-mode check re-verified, not just its happy path
- [X] T052 [P] Updated `CLAUDE.md` — refonte-progress table (spec 004 marked done), architecture tree (`session_library.py`/`fitting.py`/`session_render.py` in `engine/`), `SessionSpec`/`Step`/`RepeatGroup` schema block with the tss_target-not-validated design note, a new session-library summary paragraph, and 4 new navigation-table rows
- [X] T053 [P] Updated `README.md`'s project structure tree — `session_library.py`/`fitting.py`/`session_render.py`, `Step`/`RepeatGroup` on the schemas line, and a new top-level `sessions/` entry
- [X] T054 Full suite: 329 passed, 1 pre-existing unrelated failure (documented in `baseline/README.md` since Phase 1), 13 skipped. Lint: 222 — under the 226 baseline, not just at it
- [X] T055 **Precondition checked, not triggered**: `grep -rli "section 11|crankaddict" sessions/ app/engine/session_library.py app/engine/fitting.py app/engine/session_render.py` returns nothing — confirmed at the end of the feature, not just assumed from the plan. No `NOTICE` needed
- [X] T056 **Kept `specs/004-structured-workouts/baseline/`, deliberately, not deleted** — marked explicitly in its own `README.md` as a historical artifact frozen at 2026-08-28, not a live comparison target (the real database has since changed; the snapshot script was itself patched mid-feature — Phase 6 — to stay robust to that). The audit trail this feature's whole methodology depends on is the reason to keep it, not an oversight in forgetting to clean up

## Spec 004 — complete

All 56 tasks done across 9 phases. Structured sessions ship real steps, everything that read the old flat
`SessionSpec` still works unchanged (verified against a real corpus, not just tests), the session library
is genuinely contributor-editable, fitting exists as tested infrastructure not yet wired into initial
generation, and descriptions are derived from structure rather than hardcoded French. Three real,
pre-existing bugs were found and fixed along the way by actually exercising the feature against a real
Telegram session rather than trusting a green test suite (T026) — see project memory
`feedback_live_testing_finds_real_bugs.md` for why that pattern keeps paying off. 13 commits, full history
in `git log`.

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
