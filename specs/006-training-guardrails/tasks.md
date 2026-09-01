---
description: "Task list for 006-training-guardrails"
---

# Tasks: Training Guardrails

**Input**: Design documents from `/specs/006-training-guardrails/` (spec.md, plan.md, research.md, data-model.md, contracts/guardrails.md, quickstart.md)

**Prerequisites**: spec 002 (wellness capture — HRV/RHR/sleep retained), spec 004 (`plan_modifier`), spec 005 (`authorize_publication`). All shipped.

**Tests**: Included — the spec's FR-022a explicitly requires tests for each threshold *including the non-firing cases*, and quickstart.md names specific pytest files. Tests are part of this feature's definition of done.

**Organization**: Tasks grouped by user story (spec.md US1–US6, priority order) after a shared Setup/Foundational phase.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US6
- Every task names its exact file path

## Path Conventions

Existing single-project layout (`app/`, `tests/`, `scripts/`) — see plan.md's Project Structure.

---

## Phase 1: Setup

**Purpose**: Skeleton files and the threshold registry, which everything downstream reads from.

- [X] T001 [P] Create skeleton files with module docstrings for: `app/engine/guardrails.py`, `app/engine/baselines.py`, `app/services/guardrail_service.py`, `app/services/response_verification.py`, `app/db/models/guardrail.py`, `app/db/repositories/guardrail_repo.py`, `scripts/guardrail_state.py`, `scripts/verify_corpus.py`
- [X] T002 Create `app/engine/guardrail_thresholds.py` — every threshold from contracts/guardrails.md §1 as a module-level constant, each with its published source in a comment on the same line or the line above (FR-016, SC-007); resolve `ACWR_MIN_CTL` per research open question 2 and record the reasoning in the file; include the EWMA-vs-Gabbett caveat as a module docstring paragraph, not a footnote

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The schema, the corrected engine primitive, and the baseline machinery every user story reads.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Add `ramp_rate` (`Float`, nullable) to `Wellness` in `app/db/models/wellness.py` per data-model.md §`wellness.ramp_rate`; extend the model docstring to note it is the source's own CTL-gain-per-week, consumed as-is (research R4)
- [X] T004 [P] Define `ResponseCheckFailure` and `GuardrailAcknowledgement` ORM models in `app/db/models/guardrail.py` per data-model.md §Persisted (`ResponseCheckFailure`: `failure_kind`, `metric_name`, `stated_value`, `expected_value`, `response_excerpt`, `occurred_at`; `GuardrailAcknowledgement`: `finding_kind`, `occurrence_key`, `decision`, `decided_at`)
- [X] T005 Register the three models in `app/db/models/__init__.py` and generate the Alembic migration via `alembic revision --autogenerate` in `migrations/versions/` (depends on T003, T004)
- [X] T006 Ingest `rampRate` in `app/providers/intervals/wellness.py::ingest_wellness()` — add `ramp_rate=record.get("rampRate")` to the `wellness_repo.upsert()` call and the corresponding parameter in `app/db/repositories/wellness_repo.py::upsert()` (depends on T003)
- [X] T007 **[REGRESSION]** Correct Foster monotony in `app/engine/weekly_snapshot.py` — compute `mean/std` over all seven days of the window with rest days as zero load, not over training days only (research R2); `monotony_index` is `None` only when the window has fewer than 2 days of *any* data, not fewer than 2 training days
- [X] T008 [P] Extend `tests/test_engine/test_weekly_snapshot.py` — assert monotony includes rest days as zero; assert a real varied week (loads like `[0,0,108,63,0,310,338]`) comes out below `MONOTONY_HIGH`; assert the old training-days-only path is gone (depends on T007)
- [X] T009 Update the two monotony call sites in `app/services/weekly_recap.py` (lines ~112 and ~215) and the one in `app/llm/activity_analysis.py` (~289) to read the corrected value — confirm by grep that no call site still assumes the pre-correction scale; the "⚠️ Charge monotone" string must now be absent on a genuinely varied week (depends on T007)
- [X] T010 [P] Implement `app/engine/baselines.py`: `rolling_baseline(values: list[tuple[date, float]], *, today, window_days, min_samples) -> float | None` — the athlete's own rolling mean over the window; returns `None` below `min_samples` (FR-013); a pure function, no DB (depends on T002)
- [X] T011 [P] Test `baselines.py` in `tests/test_engine/test_guardrails_sufficiency.py` — baseline is `None` below `BASELINE_MIN_SAMPLES`; baseline reflects only the supplied history; adding rows across the minimum makes it non-`None` with no other change (FR-015, US5 acceptance 4)

**Checkpoint**: schema migrated, monotony fixed at every call site, baseline machinery in place and tested.

---

## Phase 3: User Story 1 - The athlete is warned before digging the hole (Priority: P1) 🎯 MVP

**Goal**: The coach states the acute:chronic ratio with its range, evaluates ramp rate as a second signal, raises monotony as a risk in its own right, and always says what to do about it.

**Independent Test**: Feed a load history that ramps beyond the safe range and confirm the coach raises it before prescribing more load, with the figure stated (quickstart Scenario 1).

### Tests for User Story 1

- [X] T012 [P] [US1] Test the workload signal evaluators in `tests/test_engine/test_guardrails_workload.py` — ratio inside range → no finding (FR-005); ratio above range → finding whose `action` reduces load (FR-003); ramp above `RAMP_RATE_CAUTION` → independent finding; a falling ratio + falling ramp (taper shape) is distinguishable from a rising ratio + flat ramp (build shape); monotony above `MONOTONY_HIGH` → finding
- [X] T013 [P] [US1] Test that every `GuardrailFinding` carries a non-empty `observed`, `reference`, and `action`, and that constructing one without an action fails (FR-027, SC-003), in `tests/test_engine/test_guardrails_workload.py`

### Implementation for User Story 1

- [X] T014 [US1] Define `GuardrailFinding` dataclass in `app/engine/guardrails.py` per data-model.md §Guardrail finding (`kind`, `observed`, `reference`, `threshold`, `action`, `severity`, `occurrence_key`) — `action` is a required field, an actionless finding is unconstructible
- [X] T015 [US1] Implement `evaluate_acwr(atl, ctl) -> GuardrailFinding | None` in `app/engine/guardrails.py` — ratio from `ATL/CTL` (research R3), evaluated against `ACWR_SAFE_LOW`/`ACWR_SAFE_HIGH`; `None` (not meaningful yet) when `ctl < ACWR_MIN_CTL`; above range → `action` reduces load (FR-003)
- [X] T016 [US1] Implement `evaluate_ramp_rate(ramp_rate) -> GuardrailFinding | None` in `app/engine/guardrails.py` — against `RAMP_RATE_CAUTION`/`RAMP_RATE_HIGH`, consumed as-is (research R4)
- [X] T017 [US1] Implement `evaluate_monotony(monotony_index) -> GuardrailFinding | None` in `app/engine/guardrails.py` — against `MONOTONY_HIGH`, using the corrected value from T007; `None` when `monotony_index is None`
- [X] T018 [US1] Implement `assemble_workload_findings(session, user_id) -> list[GuardrailFinding]` in `app/services/guardrail_service.py` — reads latest `wellness` row (ATL/CTL/ramp_rate) + `compute_weekly_snapshot` for monotony, calls the three evaluators, returns the non-`None` findings sorted by `severity` (depends on T015, T016, T017)
- [X] T019 [US1] Surface workload findings in the coach's context — extend `app/llm/tools.py::build_system_prompt()` with a `guardrail_findings` parameter rendered as a "SIGNAUX" block (observed value, reference, action per finding, contracts §2), wired from `app/llm/chat.py` calling `assemble_workload_findings` (depends on T018)
- [X] T020 [US1] Add the load-reduction constraint to `app/llm/prompts.py` — when a workload finding is present with ratio above range, the system prompt states that any load recommendation must reduce, not increase, load (FR-003, SC-008)

**Checkpoint**: US1 independently functional — a ramping load history produces a stated, actionable warning; a normal one produces silence.

---

## Phase 4: User Story 2 - The coach notices the athlete is not recovered (Priority: P1)

**Goal**: HRV, resting HR and sleep evaluated against personal baselines; multiple poor signals compound; every raised signal states value, baseline, and threshold.

**Independent Test**: Supply wellness data below the athlete's established baseline and confirm the coach raises it, naming the signal and the deviation (quickstart Scenario 2 — fixtures, the data does not exist live per research R1).

### Tests for User Story 2

- [X] T021 [P] [US2] Test recovery evaluators in `tests/test_engine/test_guardrails_recovery.py` with synthetic `wellness` rows — HRV >20% below baseline → easy-day finding (FR-007); RHR ≥5 bpm above baseline → fatigue finding (FR-008); both poor → combined finding ranked above either alone (FR-009); all normal → nothing (US2 acceptance 4); each finding states observed value, baseline, threshold (FR-010)
- [X] T022 [P] [US2] Test that a recovery finding against a hard prescribed session yields an explicit conflict statement, not a silent resolution (FR-012), in `tests/test_engine/test_guardrails_recovery.py`

### Implementation for User Story 2

- [X] T023 [US2] Implement `evaluate_hrv(observed, baseline) -> GuardrailFinding | None` and `evaluate_resting_hr(observed, baseline) -> GuardrailFinding | None` in `app/engine/guardrails.py` — against `HRV_DROP_PCT` and `RHR_RISE_BPM`; `None` when `observed is None` or `baseline is None` (FR-013, FR-014)
- [X] T024 [US2] Implement `combine_recovery_findings(findings: list[GuardrailFinding]) -> list[GuardrailFinding]` in `app/engine/guardrails.py` — when ≥2 recovery findings are present, emit a single higher-severity combined finding in their place (FR-009)
- [X] T025 [US2] Implement `assemble_recovery_findings(session, user_id, *, prescribed_session=None) -> list[GuardrailFinding]` in `app/services/guardrail_service.py` — builds baselines via `baselines.rolling_baseline` from `wellness` history, reads today's row, calls the evaluators, applies `combine_recovery_findings`; when a finding coincides with a hard `prescribed_session` the finding's `action` names the conflict openly (FR-012) (depends on T023, T024)
- [X] T026 [US2] Extend the `guardrail_findings` context block (T019) to include recovery findings, and pass the day's prescribed `SessionSpec` into `assemble_recovery_findings` from `app/llm/chat.py` (depends on T025)

**Checkpoint**: US1 + US2 — both workload and recovery signals raised with their numbers; combined recovery signals compound; conflicts with the plan stated openly.

---

## Phase 5: User Story 3 - The coach's numbers are true (Priority: P1)

**Goal**: Every value a coaching response states about the athlete's training matches what was retrieved; unretrieved metrics get no value; failing claims are withheld and recorded.

**Independent Test**: Generate coaching responses across many situations and check every figure against the retrieved data (quickstart Scenario 3).

### Tests for User Story 3

- [X] T027 [P] [US3] Test `MetricRegistry` and claim extraction in `tests/test_llm/test_response_verification.py` — a `(metric term, number)` pair matching the registry within `VERIFY_TOLERANCE_PCT` passes (display rounding 45.9→"46"); a mismatch is flagged; a number anchored to a metric absent from the registry is flagged `unretrieved` (FR-018); a bare number with no metric term nearby is *not* flagged (research R5 — a real reply's twelve durations/zones must not raise twelve false alarms)
- [X] T028 [P] [US3] Test that a failed check withholds the specific claim and records a `ResponseCheckFailure`, leaving the rest of the response intact (FR-019, FR-021, research R6), in `tests/test_llm/test_response_verification.py`
- [X] T029 [P] [US3] Test determinism — the same response + registry produces the same check result every run, no clock or randomness in the path (FR-022, SC-010), in `tests/test_engine/test_guardrails_determinism.py`

### Implementation for User Story 3

- [X] T030 [US3] Implement `MetricRegistry` in `app/services/response_verification.py` — a `{name: value}` collector with `register(name, value)` and `get(name)`; the definition of "retrieved" (data-model.md §Response check)
- [X] T031 [US3] Populate the registry while the context is built — in `app/llm/chat.py`, register every metric put in front of the model (CTL, ATL, TSB, TSS figures, FTP, ACWR, ramp_rate, monotony, any recovery values) as it is added to the prompt (depends on T030)
- [X] T032 [US3] Implement `verify_response(text, registry) -> VerificationResult` in `app/services/response_verification.py` — keyword-anchored `(metric term, number)` extraction per contracts §3, comparison with tolerance, classification into pass / `mismatch` / `unretrieved`; deterministic, no LLM call (FR-022)
- [X] T033 [US3] Implement claim withholding in `app/services/response_verification.py` — `apply_result(text, result) -> str` replaces each failed claim's sentence with an honest omission ("je n'ai pas ce chiffre sous la main" style), never rewrites around a corrected number (research R6), never drops the whole response
- [X] T034 [US3] Implement `guardrail_repo.record_check_failure(...)` in `app/db/repositories/guardrail_repo.py` and call it from `verify_response` for every `mismatch`/`unretrieved`, storing the claim verbatim and the expected value (FR-021) (depends on T005)
- [X] T035 [US3] Wire verification into the chat path — in `app/llm/chat.py`, after `run_agentic_loop` returns, call `verify_response` then `apply_result` before returning `response_text`; when required data could not be retrieved, the response says so rather than answering around it (FR-020) (depends on T031, T032, T033, T034)
- [X] T036 [P] [US3] Implement `scripts/verify_corpus.py --last N` — runs `verify_response` over the last N assistant `chat_messages` and prints each claim, its verdict, and the false-positive candidates for manual review (quickstart Scenario 3)

**Checkpoint**: US1–US3 — the guardrail numbers reach the athlete and are verified true before delivery; failures are counted.

---

## Phase 6: User Story 4 - Guardrails advise; they do not seize control (Priority: P2)

**Goal**: A firing guardrail mutates nothing; acceptance flows through the existing approval path; a decline is not re-raised for the same occurrence but the signal keeps being stated.

**Independent Test**: Trigger every guardrail and confirm none mutates the plan or the calendar without the athlete agreeing (quickstart Scenario 4).

### Tests for User Story 4

- [X] T037 [P] [US4] Test that `assemble_workload_findings` / `assemble_recovery_findings` perform zero writes — no plan mutation, no calendar call (FR-023, SC-006), in `tests/test_services/test_guardrail_authority.py`
- [X] T038 [P] [US4] Test the `occurrence_key` suppression — evaluating twice within the same occurrence key yields a finding the first time and a suppressed-recommendation (but still-present signal) the second; across a day boundary the recommendation returns (FR-025, FR-026), in `tests/test_services/test_guardrail_authority.py`
- [X] T039 [P] [US4] Test that an accepted recommendation is dispatched to `plan_modifier` / the spec-005 approval path and nowhere else — grep-style assertion that `guardrail_service` imports no write verb of its own (FR-024, SC-006), in `tests/test_services/test_guardrail_authority.py`

### Implementation for User Story 4

- [X] T040 [US4] Implement `occurrence_key` on each finding in `app/engine/guardrails.py` — `f"{kind}:{finding_date.isoformat()}"` per data-model.md (kind + the day the finding is about), so a decline settles that day and the next day's evaluation is genuinely new
- [X] T041 [US4] Implement `guardrail_repo.get_acknowledgement(user_id, occurrence_key)` and `record_acknowledgement(user_id, finding_kind, occurrence_key, decision)` in `app/db/repositories/guardrail_repo.py` (depends on T005)
- [X] T042 [US4] In `app/services/guardrail_service.py`, filter findings before narration — a finding whose `occurrence_key` has a `declined` acknowledgement keeps appearing in the context block but its `action` is demoted from a recommendation to a restatement of the signal (FR-025, FR-026) (depends on T040, T041)
- [X] T043 [US4] Wire finding acceptance through the existing paths — when the athlete accepts a guardrail's proposed change in chat, route it through `app/engine/plan_modifier.py` (plan changes) or the spec-005 `authorize_publication` flow (calendar), recording a `GuardrailAcknowledgement` with `decision="accepted"`; the decline branch records `decision="declined"` and does nothing else (FR-024, FR-025)

**Checkpoint**: US1–US4 — guardrails are advisory in fact, not just in intent; declines stick without silencing the signal.

---

## Phase 7: User Story 5 - Guardrails stay quiet when they cannot know (Priority: P2)

**Goal**: No signal fires without sufficient history; a present baseline with an absent current reading reports "unknown", never "normal"; a single outlier fires nothing.

**Independent Test**: Supply insufficient history and confirm no signal fires, with the insufficiency stated (quickstart Scenario 5 — the state the real account is actually in, per research R1).

### Tests for User Story 5

- [X] T044 [P] [US5] Test the insufficiency gate in `tests/test_engine/test_guardrails_sufficiency.py` — below `BASELINE_MIN_SAMPLES` no recovery finding fires and the insufficiency is expressible (FR-013, SC-004)
- [X] T045 [P] [US5] Test "baseline present, current reading absent" — a full baseline history plus `today's row = None` for that signal yields no finding and an explicit "cannot judge" state, never a comparison against the stale baseline (FR-014, research R1), in `tests/test_engine/test_guardrails_sufficiency.py`
- [X] T046 [P] [US5] Test the outlier guard — one reading beyond `OUTLIER_SD` from baseline, in otherwise normal data, fires nothing (FR-011, SC-005); the same value sustained across ≥2 days does fire, in `tests/test_engine/test_guardrails_sufficiency.py`

### Implementation for User Story 5

- [X] T047 [US5] Implement the outlier guard in `app/engine/guardrails.py` — a signal value beyond `OUTLIER_SD` standard deviations from its baseline is not evaluated on its own; a finding requires the threshold crossed on ≥2 consecutive days (FR-011)
- [X] T048 [US5] Implement an `InsufficiencyReason` return path in `app/services/guardrail_service.py` — when a recovery signal cannot be evaluated, `assemble_recovery_findings` also returns a structured reason (no baseline / no current reading / stale-only) so the context block can state it where relevant (FR-013) rather than being silently empty
- [X] T049 [US5] Render the insufficiency in the coach's context — extend the `guardrail_findings` block (T019) so a "cannot yet judge recovery: no RHR since <date>, no HRV" line appears when relevant, and confirm no wording implies signals are normal (FR-014, contracts §2) (depends on T048)
- [X] T050 [P] [US5] Implement `scripts/guardrail_state.py --describe` — per signal: days with data, date range, whether a baseline is establishable, whether a current observation exists (quickstart Scenario 0)

**Checkpoint**: US1–US5 — the guardrails are honest about their own blind spots; the real account's empty-recovery state produces silence with a reason, not false calm.

---

## Phase 8: User Story 6 - The athlete knows what this is not (Priority: P3)

**Goal**: The disclaimer is present before the first coaching interaction and in documentation; illness-consistent signals get a referral; reported pain gets no diagnosis.

**Independent Test**: Confirm the disclaimer is presented at first use and in documentation, and that health-suggestive situations produce a referral rather than advice (quickstart Scenario 6).

### Tests for User Story 6

- [X] T051 [P] [US6] Test that the disclaimer constant is emitted at the end of `/setup` onboarding, in `tests/test_bot/` (or the nearest existing setup-router test module) (FR-028, SC-009)
- [X] T052 [P] [US6] Test the prompt rules — a fixture context with illness-consistent signals produces a referral phrase; a message describing pain produces no diagnostic language (FR-029, FR-030), in `tests/test_llm/` against `build_system_prompt` output or a prompt-rule unit

### Implementation for User Story 6

- [X] T053 [US6] Add `DISCLAIMER_TEXT` as a single constant in `app/core/persona.py` (or `app/llm/prompts.py` if persona stays uncalled) — "neither a physician nor a certified coach; proposed sessions are suggestions"; spec 007 relocates it to the first-run flow, this feature places it where a first use passes today (research R7)
- [X] T054 [US6] Emit `DISCLAIMER_TEXT` at the end of `_finalize_setup()` in `app/bot/routers/setup.py` and add it to `README.md` (FR-028, SC-009) (depends on T053)
- [X] T055 [US6] Add FR-029 (illness-consistent signals → suggest qualified advice, do not prescribe through) and FR-030 (reported pain/injury → no diagnosis) as explicit rules in `app/llm/prompts.py`, alongside the existing response rules

**Checkpoint**: all six user stories independently testable.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T056 [P] Run the full quickstart.md scenario sequence (0–7); confirm every "Definition of done" item, especially the verifier's false-positive rate on the real corpus (hand-reviewed, not just catch rate) and "baseline present, observation absent" reporting unknown
- [X] T057 [P] Run `pytest tests/` and `ruff check app/ tests/`; fix any violation this feature introduced (Constitution Principle V, FR-022a)
- [X] T058 Update `CLAUDE.md` — new "Garde-fous d'entraînement (spec 006)" section, the `wellness.ramp_rate` column and two new tables, `guardrail_thresholds.py` in Navigation rapide, the **corrected monotony** noted under the weekly-snapshot rule (it changed user-visible behaviour), and the spec-005→006 status line at the top
- [X] T059 Update `.specify/memory/constitution.md` — this is the third spec to flag it stale and the first to *cite* it (FR-022a → Principle V → `tests/test_strava/`, deleted in spec 002). Bump to the appropriate version, fix PostgreSQL→SQLite, Strava→intervals.icu, `init.sql`→Alembic, `tests/test_strava/`→`tests/test_providers/`, and record the change in the Sync Impact Report. **Confirm with the maintainer before editing** — a constitution amendment is a deliberate governance act, not a feature side effect

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)**: no user story work before Phase 2 completes. T002 (thresholds) blocks nearly everything.
- **Phase 2's T007–T009 (monotony regression)** is independent of the rest of Phase 2 and can land first — it fixes a live bug and is the lowest-risk, highest-value change in the feature.
- **US1 (Phase 3)** depends only on Foundational. MVP by itself.
- **US2 (Phase 4)** depends on Foundational (baselines) and reuses US1's `GuardrailFinding` type and context block.
- **US3 (Phase 5)** is independent of US1/US2's evaluators — it verifies whatever numbers reach the response — but shares the `app/llm/chat.py` context-assembly point, so coordinate edits there.
- **US4 (Phase 6)** depends on US1/US2 producing findings to govern.
- **US5 (Phase 7)** depends on US2's evaluators (it constrains when they fire) and Foundational's baseline machinery.
- **US6 (Phase 8)** is independent of all evaluator work.
- **Polish (Phase 9)** depends on all user stories.

Within a phase, `[P]` tasks touch different files; unmarked tasks are sequential (same-file edits or a stated dependency).

## Implementation Strategy

**Land the regression first.** T007–T009 fix a false alarm the athlete sees today. It is inside Phase 2 but has no dependency on T002–T006 and is the single most defensible change here — do it, commit it, verify the message disappears on a varied week, before anything else.

**MVP = US1 + US3** (not US1 + US2). US1 is the headline capability and US3 is what makes any guardrail number trustworthy — a warning the athlete cannot verify is worth little (spec.md US3: "every guardrail in this specification is worthless if its figures might be fabricated"). US2 is P1 in the spec but ships unvalidated against real data (research R1), so it is second in build order despite equal priority.

**Then incrementally**: US2 (recovery, fixture-tested) → US4 (advisory discipline, once findings exist to govern) → US5 (the honesty gate, which the real account exercises immediately) → US6 (disclaimer and scope).

Each user story phase ends at a checkpoint where the feature built so far is independently demonstrable per quickstart.md's numbered scenarios.
