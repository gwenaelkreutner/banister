---
description: "Task list for feature 002 — intervals.icu as sole training data source"
---

# Tasks: intervals.icu as Sole Training Data Source

**Input**: Design documents from `/specs/002-intervals-icu-provider/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/sport-provider.md](./contracts/sport-provider.md),
[quickstart.md](./quickstart.md)

**Tests**: Required. FR-034c mandates it, and FR-020's "unknown is not zero" governs 15% of this athlete's
real activities (research R9c) — a property that fails silently and can only be caught by assertion.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task serves
- Exact file paths are included in every task

## Organization note — the same deliberate deviation as spec 003

Phases below follow **plan.md's A–F sequence** (build alongside → switch → remove), not one phase per user
story. The reason is the same as in spec 003, and it is real rather than convenient: US1 (connecting),
US2 (a ride becomes a coaching moment) and US3 (the numbers match) are not separable slices — the second
cannot ship without the client from the first and the mapper from the third. Story labels are retained on
every task, and the mapping is stated at the end.

Two stories *are* genuinely separable and are called out where they sit: **US5 (wellness capture)** depends
on nothing but the client, and **US4 (history import)** depends only on the client and mapper.

## What verification changed before any code was written

Research R9 measured the real API and **overturned the plan's central assumption**. This reshaped the task
list substantially, so it is stated up front rather than buried:

- **Far more deletion than anticipated.** `variability_index`, `cardiac_drift_index`, `dominant_zone` and
  the whole `time_in_zones_s` computation are provided by the source. So is the Banister-TRIMP HRSS
  implementation in `app/engine/tss.py` (`hr_load`, `hr_load_type: "HRSS"`, `trimp`).
- **Less new code than anticipated.** No streams fetch is needed for the quality metrics after all —
  `decoupling` arrives on the activity payload.
- **Only two computations are genuinely ours**: `respect_zones_score` and `session_type_real`, both
  because they need the training plan, which the source cannot know.

---

## Phase 1: Setup

**Purpose**: Fixtures and baseline — the measurements everything else is judged against

- [ ] T001 Record the pre-change baseline in `specs/002-intervals-icu-provider/baseline.md`: exact passing/skipped test counts and exact `ruff check app/ tests/` violation count, with the commands used
- [ ] T002 [P] Capture real API fixtures into `tests/fixtures/intervals/`: an activity **with** full sensor data, an activity **with no sensors** (null `icu_training_load`), a wellness range, an athlete profile, and one `icu_intervals` payload — recorded from the live account, not handwritten
- [ ] T003 [P] Create the `app/providers/` package skeleton with `intervals/` and `analysis/` subpackages

**Why T002 matters more than it looks**: handwritten fixtures encode the same assumptions the code makes,
so they pass whether or not the mapping is right. The null-sensor fixture in particular is what makes
FR-020's test meaningful — research R9c found 8 of 54 real activities in that state.

**Checkpoint**: baseline recorded, real fixtures on disk.

---

## Phase 2: The client (Plan Phase A) — US1

**Goal**: Connecting is pasting one key. Nothing else changes yet; Strava still runs.

**Independent Test**: Start with a valid key and see the bound athlete named; start with a bad key and see
startup refuse.

- [X] T004 [US1] Implement `app/providers/intervals/errors.py`: distinct exception types for credential-rejected, rate-limited, and transient failure — the contract requires callers to tell these apart (FR-005, FR-013), and collapsing them into one type makes both unimplementable above the boundary
- [X] T005 [US1] Implement `app/providers/intervals/client.py` with basic auth (`API_KEY` as username), covering: athlete profile, activities by date window, wellness by date window, one activity in full, and activity streams
- [X] T006 [US1] Make `intervals_api_key` required in `app/config.py`, and treat `intervals_athlete_id` as optional — `0` resolves to the key's own athlete (research R9e), so requiring it would be friction with no benefit
- [X] T007 [US1] Verify the credential at startup in `app/db/lifecycle.py` or a sibling: identify the bound athlete, refuse to start with a message naming the setting and where to get a valid key (FR-002, FR-003)
- [X] T008 [US1] Store the key encrypted at rest, refusing to persist it rather than writing it unencrypted when secure storage is unavailable (FR-004) — mirrors the posture spec 001 established. Resolution: the app never persists this credential itself (it only reads what the operator wrote to `.env`, same as every other secret in this project), so FR-004's "refuse to persist unencrypted" has no write path to govern; keyring/Fernet-style storage would also never work in the primary docker-compose deployment target (no OS keychain in a container). Implemented the part of FR-004 that does apply: `intervals_api_key` is `pydantic.SecretStr`, so the raw value can't leak via `repr(settings)`, tracebacks, or an incidental debug log. See the comment on the field in `app/config.py`.
- [X] T009 [US1] Write `tests/test_providers/test_client.py` against the T002 fixtures: each error type is raised for its condition; the athlete endpoint identifies correctly
- [X] T010 [US1] Verify against the **live** account that startup succeeds with the real key and refuses with a corrupted one

**Checkpoint**: the app connects and identifies the athlete. Strava is untouched and still working.

---

## Phase 3: The mapper, and the deletions (Plan Phase B) — US3

**Goal**: An intervals.icu payload becomes an `AnalyzedSession` whose numbers match the source exactly.

**Independent Test**: Map the fixtures; assert every consumed value equals the source's, and every omitted
value is `None`.

### The mapping

- [X] T011 [US3] Implement `app/providers/intervals/mapper.py`: payload → `AnalyzedSession`, consuming `icu_training_load`, `icu_intensity` (÷100 — the source expresses IF as a percentage), `icu_weighted_avg_watts`, `icu_zone_times`, `icu_variability_index`, `decoupling`, and the context fields. `icu_ctl`/`icu_atl` are **not** mapped here — `AnalyzedSession` has no ctl/atl/tsb fields; that fitness state is written by `app/strava/webhook.py` from the local `atl_ctl` engine after analysis, and switching it to consume `icu_ctl`/`icu_atl` is a Phase 6 (cutover) change to webhook.py, not something the mapper can do alone. Documented in the mapper's module docstring.
- [X] T012 [US3] Write `tests/test_providers/test_mapper.py` asserting **per field** that an omitted source value maps to `None` and never `0.0` — using the null-sensor fixture from T002, not the populated one (FR-020)
- [X] T013 [US3] Assert in the same test that per-activity values are written once at ingestion and never recomputed on read, so a later threshold change cannot retroactively alter stored history (FR-022). The mapper takes no threshold parameter at all (asserted directly on its signature), so there is nothing for a later threshold change to retroactively apply to.

### The deletions — larger than the spec anticipated (research R9a)

- [X] T014 [P] [US3] Remove `_compute_normalized_power` from the analyzer — `icu_weighted_avg_watts` provides it. **Scope note**: only the stream-based rolling-30s/4th-power *computed* branch was removed. The passthrough of Strava's own `weighted_avg_power` field was kept — that's a source value, not a local computation, and removing it would have been a pure regression for the Strava path with no corresponding benefit.
- [X] T015 [P] [US3] Remove `_compute_time_in_zones` and `_accumulate_zones` — `icu_zone_times` provides it, and includes a sweet-spot bucket we never computed. For the (temporarily reduced) Strava path, `time_in_zones_s` is now always `{}`.
- [X] T016 [P] [US3] Remove `_cardiac_drift_index` — `decoupling` provides it. **Note**: the two disagree materially (22.9% vs 15.7%, four reconciliation attempts failed — research R9b). That disagreement is the argument for consuming theirs, not against it. For the Strava path, `cardiac_drift_index` is now always `None`.
- [X] T017 [P] [US3] Remove local `variability_index` computation — verified numerically identical to `icu_variability_index`. **Scope note**: the NP/avg_power arithmetic itself was kept (it is two-value division, not "computation" in the sense this task targets) — only the stream-based NP that used to feed it is gone (T014), which naturally narrows when this ever produces a value for Strava.
- [X] T018 [P] [US3] ~~Remove~~ **Deferred** — `calc_tss` and `calc_hrss` are **not yet removed** from `app/engine/tss.py`. Discovered during implementation: `app/strava/history.py` (the Strava historical import, untouched until Phase 7) still calls `calc_tss` directly — deleting it now would `ImportError` the whole app at startup, not just degrade the analyzer. What *is* done: `app/strava/analyzer.py`'s live per-activity analysis path (`SessionAnalyzer.analyze()`) no longer calls either function — `tss` and `fatigue_anomaly` are always `None` from that path now, achieving T018's actual goal (no redundant per-activity TSS computation on the path intervals.icu supersedes) without the unsafe premature deletion. Full removal of `calc_tss`/`calc_hrss` (and T019 below) is deferred to Phase 7, when `history.py` itself is removed alongside the rest of the Strava path.
- [X] T019 [US3] ~~Remove the tests that covered only the deleted calculations~~ **Deferred alongside T018** — `test_tss.py`/`test_hrss.py` still cover functions still in real use by `history.py`; removing them now would violate FR-034c ("without weakening coverage of anything that remains"). Both files verified still passing, untouched. **What was actually removed**: the `TestHRSSIntegration` class and TSS/zone assertions in `tests/test_strava/test_session_analyzer.py` — these tested `SessionAnalyzer.analyze()`'s (now-removed) HRSS routing, sex-parameter handling, and fatigue-anomaly-via-HRSS, which is exactly "covered only by a deleted calculation" even though it wasn't in the original file list. `test_session_analyzer.py` was rewritten rather than deleted, to keep coverage of what remains (NP passthrough, VI arithmetic, the empty-zones/no-TSS reduced behavior itself).

### What stays

- [X] T020 [US3] Keep `_compute_respect_zones_score`, but rebuild it on the source's `icu_zone_times` rather than our own zone accumulation — it still needs the plan, which the source cannot know. Rebuilt in `mapper.py` (own implementation, since the source doesn't share `AnalyzedSession`-construction code with the Strava path). Also added an explicit empty-zones guard (return `None`, not a real `0.0`) — needed once the Strava path's `time_in_zones_s` became always-empty (T015), so its score reads as "unknown" rather than "0% compliance" (FR-020). Applied to both `analyzer.py`'s copy and `mapper.py`'s.
- [X] T021 [US3] Keep `_detect_session_type` — our own classification, unavailable from the source. Rebuilt in `mapper.py` against primitives (`duration_s`, `time_in_zones_s`, `race`, `high_intensity_variation`) rather than a `RawActivity` — `mapper.py` works on raw intervals.icu payload dicts, not `RawActivity` (that stays Strava-specific machinery). The "high intensity variation" signal is now the source's own `icu_intervals` WORK-segment detection rather than our P95/P05 stream heuristic (stronger signal, no streams fetch needed).
- [X] T022 [US3] Rebuild `_intervals_consistency_index` on the source's `icu_intervals` detection (9 intervals with full metrics per activity) rather than our own effort-block detection. Same coefficient-of-variation formula, fed by `icu_intervals` entries where `type == "WORK"` instead of our own threshold/block detection over raw watts streams.
- [X] T023 [US3] Run the full suite and confirm it matches the T001 baseline minus only the tests removed in T019 — **adjusted**: baseline (190) + Phase 2's 17 new client tests + Phase 3's 31 new mapper tests, minus the `TestHRSSIntegration` class and superseded assertions in `test_session_analyzer.py` (the actual T019 scope, per the note above) = 200 passed, 14 skipped, 0 failed. Confirmed.

**Checkpoint**: the mapper produces correct `AnalyzedSession` values. Nothing consumes it yet.

---

## Phase 4: Storage (Plan Phase C) — US5

**Goal**: Wellness and sync state persist. US5 is genuinely independent of the notification path.

- [ ] T024 [P] [US5] Create `app/db/models/wellness.py`: dated recovery signals, **every signal nullable** — a missing HRV stored as `0` would later read as a catastrophic drop once spec 006 evaluates thresholds (FR-024)
- [ ] T025 [P] Create `app/db/models/sync_state.py`: reported markers keyed by the source's activity id, plus last-successful-refresh and history-import progress (FR-009, FR-021, FR-027)
- [ ] T026 Generate the Alembic revision for both tables via `alembic revision --autogenerate` — never a hand-written migration (the mechanism spec 003 established exists for exactly this)
- [ ] T027 [P] [US5] Implement `app/db/repositories/wellness_repo.py`
- [ ] T028 [P] Implement `app/db/repositories/sync_state_repo.py`
- [ ] T029 [US5] Implement wellness ingestion in the client path and store it — **capture only, no interpretation** (FR-025); readiness belongs to spec 006
- [ ] T030 [US5] Write `tests/test_providers/test_wellness.py` asserting a missing reading is stored as unknown and stays distinguishable from zero
- [ ] T031 Write a test asserting a reported marker survives a restart — the property FR-009 depends on and the one an in-memory implementation would silently fail

**Checkpoint**: wellness captured, sync state durable. US5 is complete and independently verifiable.

**Reality check on US5**: research R9d found this athlete's wellness entirely empty — 0 of 8 days have
`hrv`, `restingHR`, `sleepSecs` or `readiness`. The capture is still correct to build; it stores what
exists. But **spec 006's readiness guardrails have no input today**, and that is worth confirming before
spec 006 is planned rather than discovering during it.

---

## Phase 5: The poller and history (Plan Phase D) — US4

**Goal**: New activities are detected. Nothing is announced yet — that is the next phase, deliberately.

- [ ] T032 Implement `app/providers/intervals/poller.py`: query a moving date window, compare against reported markers, at the five-minute interval FR-007 mandates
- [ ] T033 Make the interval configurable with an enforced floor that protects the source's quota (FR-007, FR-008)
- [ ] T034 Bound the number of notifications produced when many activities are detected at once — **every activity still ingested, not every one announced** (FR-011)
- [ ] T035 Ensure overlapping refreshes cannot double-ingest or corrupt state (FR-014)
- [ ] T036 Implement retry for transient failures that neither exhausts the quota nor alerts the athlete on every attempt (FR-013). The rate-limit response shape is still unverified (research R9f) — handle defensively and confirm from the real response when it first occurs
- [ ] T037 Wire the poller into the FastAPI lifespan in `app/main.py`, alongside the existing schedulers
- [ ] T038 [US4] Implement first-connection history import, deep enough to establish a meaningful chronic load (FR-026)
- [ ] T039 [US4] Make an interrupted import resume without duplicating and without leaving gaps (FR-027)
- [ ] T040 [US4] While importing, report that history is incomplete rather than presenting partial figures as complete (FR-028)
- [ ] T041 [US4] Handle an account with little or no history using documented conservative assumptions, disclosed to the athlete (FR-029)
- [ ] T042 Write `tests/test_providers/test_poller.py`: exactly-once detection, an edited activity is not a new one (FR-010), backlog bounded
- [ ] T043 Verify against the **live** account that the poller detects a real activity and that steady-state polling stays inside the quota budget (~6% of the daily allowance)

**Checkpoint**: detection works end to end. The athlete has noticed nothing, by design.

---

## Phase 6: Cutover (Plan Phase E) — US2 🎯 the risky one

**Goal**: The poller drives the post-activity notification. This is the phase everything before it existed
to de-risk.

**Independent Test**: Do a real ride; receive the full staged exchange, indistinguishable from before.

- [ ] T044 [US2] Extract post-activity context assembly into `app/services/activity_feedback.py`, with no aiogram dependency — so it is testable without simulating a conversation (FR-038)
- [ ] T045 [US2] Point the staged notification at the poller instead of the inbound webhook, preserving ordering, notable-aspect selection, personal-best detection, and the restraint of raising only one alert (FR-030)
- [ ] T046 [US2] Preserve perceived-exertion capture and keep it informing the feedback (FR-031)
- [ ] T047 [US2] Keep delivering feedback when the athlete never answers (FR-032)
- [ ] T048 [US2] Confirm activity-to-session matching is unchanged: ±2 days bounded by the training week, hundred-point score, no double-claiming, bonus framing when every slot is taken (FR-033, FR-034)
- [ ] T049 [US2] Mark an activity as reported **only after delivery succeeds** (FR-012) — marking before sending loses a notification whenever Telegram is briefly unreachable
- [ ] T050 [US2] Write `tests/test_services/test_activity_feedback.py` covering context assembly without a bot
- [ ] T051 [US2] **Verify against a real ride, end to end, before proceeding to Phase 7.** Not after — this is the one gate that cannot be recovered from cheaply if skipped
- [ ] T052 Verify the weekly review still produces correct results now that load values come from the source rather than local computation (FR-034a) — it is *not* unaffected
- [ ] T053 Verify the daily session reminder still functions unchanged (FR-034b)

**Checkpoint**: the athlete's experience is identical, driven by a different mechanism. Strava is now
redundant but still present.

---

## Phase 7: Removals and structural debt (Plan Phase F) — US6

**Purpose**: Delete what is now dead, and fix the four defects the spec-001 audit found while the code is
already open.

### Removals

- [ ] T054 [US6] Remove manual session entry from `app/bot/routers/session_log.py`: the command, the duration prompt, and `SessionLogStates` (FR-035)
- [ ] T055 [US6] **Preserve perceived-exertion capture** while doing so — they share an implementation today, and only one is meant to go (FR-031, FR-037). This is the single easiest thing to break in this phase
- [ ] T056 [US6] Remove `tss_from_rpe()` from `app/engine/atl_ctl.py` — no caller remains once manual entry is gone
- [ ] T057 [US6] Delete `app/strava/` in full, and `app/bot/routers/strava.py` (FR-036)
- [ ] T058 [US6] Remove the inbound Strava webhook route and the OAuth callback from `app/main.py`
- [ ] T059 [US6] Drop `oauth_connections` via an Alembic revision — spec 003 deliberately deferred this to here, since the provider it authorized was still in use then
- [ ] T060 [US6] Remove `oauth_repo` and the Strava settings from `app/config.py` and `.env.example`
- [ ] T061 [US6] Decide and act on the `activities` table (plan.md open question 3) — re-fetchable from the source, so truncate-and-repopulate is viable, but `compute_fitness_from_any()` duck-types across `SessionLog` and `Activity` and must not break
- [ ] T062 [US6] Verify no Strava reference remains: `grep -rn "strava\|Strava" app/` returns nothing

### Structural debt (FR-038..041)

- [ ] T063 Replace direct data access in the surviving handler with a repository call (FR-039)
- [ ] T064 Make personal-best detection accept the data it needs directly, removing the fabricated `_FakeAnalyzed` stand-in (FR-040)
- [ ] T065 Remove the value whose availability depends on a guard condition duplicated in two places — a latent `NameError` if either is ever edited alone (FR-041)
- [ ] T066 Remove dead imports on the surviving path

**Checkpoint**: one ingestion path, no dead alternatives, audit defects cleared.

---

## Phase 8: Polish & validation

- [ ] T067 Run every scenario in [quickstart.md](./quickstart.md), starting with Scenario 0
- [ ] T068 Run the contract check: `git diff --stat <base>..HEAD -- app/engine/ app/llm/` shows import-path updates only, no logic changes — anything more means the provider leaked past its boundary
- [ ] T069 Confirm no new lint violations beyond the T001 baseline
- [ ] T070 [P] Update `CLAUDE.md`: the Strava pipeline section, the stack line, the TSS/HRSS rules that no longer apply, and the environment variables — per the refactor banner's instruction to update *during* each migration
- [ ] T071 [P] Update `README.md`: intervals.icu setup replaces Strava's, and the "Strava is optional" framing is now false — the source is mandatory
- [ ] T072 Record the answer to plan.md open question 1 (which FTP the plan generator should use) once observed in practice: the profile reports null, the activity 290, `icu_pm_ftp` 225, `icu_rolling_ftp` 280

---

## Dependencies & Execution Order

```
Phase 1 (Setup, fixtures)
   └─> Phase 2 (Client)                    ⟵ blocks everything
          ├─> Phase 3 (Mapper + deletions)
          │      └─> Phase 5 (Poller + history)
          │             └─> Phase 6 (Cutover)  🎯 the risky one
          │                    └─> Phase 7 (Removals)
          │                           └─> Phase 8 (Validation)
          └─> Phase 4 (Storage + wellness)  ⟵ US5, independent of the notification path
```

**Phase 4 can proceed in parallel with Phase 3** — wellness capture needs only the client. Everything else
is strictly sequential, for the same reason as spec 003: it keeps every failure attributable.

### Within phases

- Phase 3: T014–T018 are all `[P]` — separate deletions in separate places
- Phase 4: T024/T025 and T027/T028 pair up as `[P]`
- Phase 8: T070/T071 are `[P]`

### The one gate that matters

**T051 must pass before Phase 7 begins.** It verifies the staged notification against a real ride while
Strava is still present as a fallback. Deleting Strava first and *then* discovering the poller-driven
notification behaves differently leaves no working path — and the post-activity loop is the product's
daily value, not a peripheral feature.

---

## Story → phase mapping

| Story | Priority | Delivered by | Verified by |
|---|---|---|---|
| US1 — Connect by pasting one key | P1 | Phase 2 | T009, T010; quickstart 1 |
| US2 — A ride becomes a coaching moment | P1 | Phase 6 | T050, **T051**; quickstart 3 |
| US3 — The coach's numbers are the athlete's | P1 | Phase 3 | T012, T013; quickstart 2, 4 |
| US4 — A new athlete does not start from zero | P2 | Phase 5 | T038–T041; quickstart 5 |
| US5 — Wellness captured for future use | P3 | Phase 4 | T030; quickstart 6 |
| US6 — The superseded paths are gone | P3 | Phase 7 | T062; quickstart 7 |

---

## Implementation Strategy

### Safe stopping points

Phases 2 through 5 all leave Strava working and the athlete's experience unchanged. They can be merged and
left in place without committing to the switch — that is where the risk reduction lives, exactly as in
spec 003.

**Phase 6 is the commitment.** Before it, the new path is inert. After it, the athlete's daily loop depends
on it.

### Recommended sequence

1. **Phases 1–3** — client, mapper, deletions. Reversible, and the deletions are validated by the existing
   suite still passing.
2. **Phase 4** — wellness. Independent; can land any time after Phase 2.
3. **Phase 5** — poller. Still announces nothing.
4. **Phase 6** — the cutover, gated on T051 against a real ride.
5. **Phases 7–8** — remove and verify.

### Before starting Phase 7

Confirm T051 actually passed against a real activity, not a fixture. Phase 7 deletes the fallback.

---

## Notes

- `[P]` means different files with no shared dependency
- Commit at every phase checkpoint; each boundary is a working state
- The highest-value tests here are T012 (null is not zero — 15% of real activities), T031 (markers survive
  restart) and T051 (the real ride). Each is also easy to write in a way that passes without proving
  anything: T012 against a fully-populated fixture tests nothing, and T051 against a recorded payload is
  not a real ride
