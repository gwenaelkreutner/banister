# Implementation Plan: Training Guardrails

**Branch**: `006-training-guardrails` | **Date**: 2026-08-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-training-guardrails/spec.md`

## Summary

Give the coach the ability to notice the athlete is heading somewhere bad — load ramping faster than it is
absorbed, training while unrecovered, a monotonous week — and to say so, with the number, the reference,
and something to do about it. And verify that what the coach says about the athlete's numbers is true.

Phase 0 measured the live account and the live database ([research.md](./research.md)) rather than assuming
the spec's premises held. Three findings reshape the work:

1. **The monotony index shipping today is wrong and is firing.** `weekly_snapshot.py` computes Foster
   monotony over training days only, dropping the rest days that create the variance. On this athlete's
   real week it reports **2.57** against a correct value of **0.79** — and three live call sites are already
   telling them *"⚠️ Charge monotone"* on a week that is unusually varied. The spec assumed uniformity was
   "already computed using an established formula" and that this feature merely surfaces it (FR-004). That
   assumption is false, and correcting it is a prerequisite rather than a cleanup (R2).
2. **The local load series double-counts by ~1.8×, so the acute:chronic ratio must come from the source.**
   A naive local computation yields ACWR 2.34 on a week the source's own figures put at 1.41. `ATL/CTL`
   read from the `wellness` table is authoritative, de-duplicated by construction, and present on rest days
   (R3). The source also publishes `rampRate`, unused today, which is a genuine second signal — this
   athlete hit **6.43 CTL/week** on 25/08 (R4).
3. **The recovery data US2 evaluates does not exist for this athlete** — no HRV ever, no sleep, and no
   resting HR since 19 July against a complete June/July baseline (R1). US2 gets built and fixture-tested;
   **US5 is the story that gets validated live**, because "baseline present, observation absent" is the
   state the real account is actually in, and it is the exact trap FR-014 names.

**This feature adds no write path.** Guardrails advise; acceptance flows through spec 004's `plan_modifier`
and spec 005's `authorize_publication`. That is what makes FR-023 and SC-006 structural rather than tested.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: SQLAlchemy async, Pydantic v2, aiogram v3 (presentation only), pytest. No new
third-party dependency — the statistics this feature needs are in `statistics` and already in use.

**Storage**: SQLite via SQLAlchemy async. One new column (`wellness.ramp_rate`) and two small tables
(`response_check_failures`, `guardrail_acknowledgements`), one Alembic revision. Findings and baselines are
**derived, never stored** — see [data-model.md](./data-model.md).

**Testing**: pytest. Predominantly offline: guardrail evaluation is pure deterministic Python over captured
data, so every threshold is drivable with fixtures. This is a genuine property of the feature (FR-022), not
a shortcut — unlike spec 005, where the guarantees were about a remote service's real behaviour. The one
live check that fixtures cannot substitute for is R1's "baseline present, observation absent" state.

**Target Platform**: Self-hosted single-process Linux container

**Project Type**: Single Python application

**Performance Goals**: Not a factor. Evaluation is arithmetic over at most a few hundred rows already in
memory for the chat path.

**Constraints**:
- All evaluation is deterministic Python; the LLM narrates findings and produces none (FR-022, SC-010)
- No finding fires without sufficient history, and the baseline is the athlete's own (FR-006, FR-013)
- Missing data is unknown, never a default — including when a baseline exists but today's reading does not
  (FR-014)
- Every finding carries observed value, reference, and action (FR-027) — enforced by the type
- No plan or calendar mutation from a guardrail firing (FR-023)
- Every threshold documented with its published source, findable without reading code (FR-016, SC-007)

**Scale/Scope**: One athlete. Six guardrail signals, one verifier, one corrected engine function.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Deterministic engine, zero LLM load calculation** | **PASS, and this feature is the principle's enforcement mechanism.** FR-022 restates it; the response verifier (US3) is the first thing in this codebase that actively *checks* the LLM is not inventing numbers rather than trusting it not to. R5's decision to reject "ask a second LLM to check the first" is this principle applied to the checker itself. |
| **II. Single-user, local-first** | **PASS.** No new external service, no new data acquisition (spec Scope excludes it). Everything is computed from the local `wellness`, `activities` and `session_logs` tables. |
| **III. Clean layered architecture** | **PASS with a design obligation.** Threshold constants and signal evaluation are pure and live in `app/engine/`; assembling athlete data and producing findings lives in `app/services/`; narration and verification wrap `app/llm/`; presentation stays in `app/bot/`. The verifier is the awkward one — it must sit *around* generation without `app/llm/` importing evaluation logic, so it is a service the chat orchestrator calls, not a step inside the provider. |
| **IV. Explicit data provenance, never estimate silently** | **PASS, and load-bearing throughout.** R3 (consume the source's ATL/CTL rather than a local series that double-counts) and R4 (`rampRate` consumed as-is) are direct applications of the source-authority rule. FR-014 — missing is unknown, never a default — is this principle stated as a requirement, and R1 found the precise case where violating it would be easiest and most damaging. |
| **V. Engine logic is test-covered** | **PASS, and the spec pre-empted it.** FR-022a already requires tests for each threshold *including the cases where it must not fire*. The monotony correction lands in `app/engine/weekly_snapshot.py`, squarely inside the principle's scope, and changes behaviour three call sites already depend on — so its tests are a regression check, not a formality. |

### Finding: the constitution is now stale in a way a specification cites

Recorded in the spec 004 and spec 005 plans and still unaddressed; this is the third consecutive flag, and
this feature sharpens it from cosmetic to concrete.

The constitution describes PostgreSQL, Strava OAuth, `migrations/init.sql`, and manual migrations — none of
which have existed since specs 002/003. Spec 006's **FR-022a explicitly invokes "the project's
constitutional requirement that engine changes be test-covered"**, and Principle V names
`tests/test_strava/` as one of the two directories such tests must live in. That directory does not exist.
A requirement in a specification now points, through the constitution, at a path deleted two specs ago.

Amending it remains a separately versioned act under its own Governance section and is deliberately not
done as a side effect of a feature plan. But it has stopped being tidy-up: the document is being cited.

**Also relevant**: the constitution predates any notion of the system checking its own outputs. Principle I
forbids the LLM computing load; it says nothing about verifying the LLM's *claims* about computed load,
which is what US3 introduces. A future amendment should probably promote it.

## Project Structure

### Documentation (this feature)

```text
specs/006-training-guardrails/
├── plan.md              # This file
├── research.md          # Phase 0 output — live-measured findings
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── guardrails.md
├── checklists/
│   └── requirements.md  # pre-existing
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/engine/
├── guardrail_thresholds.py    # NEW — every threshold + its published source (FR-016, SC-007)
├── guardrails.py              # NEW — pure signal evaluation: ratio, ramp, monotony, recovery
├── baselines.py               # NEW — rolling personal baselines, None below minimum samples
└── weekly_snapshot.py         # MODIFIED — Foster monotony corrected to include rest days (R2)

app/services/
├── guardrail_service.py       # NEW — assemble athlete data, produce findings, honour declines
└── response_verification.py   # NEW — MetricRegistry, keyword-anchored claim checking

app/db/
├── models/guardrail.py        # NEW — ResponseCheckFailure, GuardrailAcknowledgement
├── models/wellness.py         # MODIFIED — ramp_rate column
└── repositories/guardrail_repo.py   # NEW

app/providers/intervals/
└── wellness.py                # MODIFIED — ingest rampRate (R4)

app/llm/
├── chat.py                    # MODIFIED — build the registry, verify before returning
├── tools.py                   # MODIFIED — findings into the coaching context
└── prompts.py                 # MODIFIED — disclaimer, no-diagnosis, referral rules (FR-028..030)

app/services/weekly_recap.py   # MODIFIED — corrected monotony (2 call sites)
app/llm/activity_analysis.py   # MODIFIED — corrected monotony (1 call site)

migrations/versions/           # NEW revision — one column, two tables

scripts/
├── guardrail_state.py         # NEW — what the athlete's data supports (quickstart Scenario 0)
└── verify_corpus.py           # NEW — run the verifier over real chat history (Scenario 3)

tests/
├── test_engine/test_guardrails_workload.py      # NEW
├── test_engine/test_guardrails_recovery.py      # NEW — fixtures; the data does not exist live (R1)
├── test_engine/test_guardrails_sufficiency.py   # NEW — the story this account actually exercises
├── test_engine/test_guardrails_determinism.py   # NEW — SC-010
├── test_engine/test_weekly_snapshot.py          # EXTENDED — the monotony regression
├── test_llm/test_response_verification.py       # NEW
└── test_services/test_guardrail_authority.py    # NEW — FR-023/FR-024/SC-006
```

**Structure Decision**: existing layout, no new top-level directory. Two judgement calls worth recording.

**Splitting `guardrail_thresholds.py` from `guardrails.py`.** SC-007 requires a reader to locate every
threshold *without reading code*. A constants module that reads as documentation satisfies that literally;
thresholds inlined at their use sites do not, however well commented. The split is the success criterion
made structural.

**`response_verification.py` is a service, not part of `app/llm/`.** It must run around generation while
staying pure and deterministic (FR-022), and Principle III forbids `llm/` performing evaluation. Putting it
in `services/` and having the chat orchestrator call it keeps `llm/` narration-only, at the cost of one
more hop in the chat path — the right trade, since the alternative quietly puts evaluation logic inside the
layer the constitution says must not evaluate.

## Complexity Tracking

No Constitution Check violations to justify.

## Phase boundaries and known scope limits

Recorded so they are decisions rather than discoveries:

1. **US2 ships unvalidated against real data, deliberately.** Research R1: no HRV, no sleep, no resting HR
   since 19 July. The thresholds are built and fixture-tested; live confirmation waits for the athlete to
   connect a device. The alternative — deferring US2 until data exists — would leave the feature unable to
   act the day data appears, and US5's insufficiency machinery already makes shipping it safe.

2. **The monotony correction changes existing user-facing output.** Three call sites currently emit
   *"⚠️ Charge monotone"* on weeks that are not monotonous. Fixing the formula makes those messages
   disappear on such weeks — an intended behaviour change, called out here so it is not later mistaken for
   a regression.

3. **Verification is keyword-anchored and will miss some fabrications** (R5). Recall is traded for
   precision because a checker that flags twelve durations to catch two claims gets switched off. SC-001
   and SC-002 are corpus measurements, and the false-positive rate is the number to watch.

4. **A failed check withholds the claim, not the response** (R6). Correcting the number is explicitly not
   attempted — rewriting a sentence around a substituted value is a generation task, and doing it
   deterministically reads worse than an honest omission.

5. **The disclaimer's delivery point belongs to spec 007.** This feature owns the text and the requirement
   and places it where a first use demonstrably passes today (end of `/setup`, README), behind a single
   constant so 007 relocates rather than rewrites it (R7).

6. **`fit_template()` stays unwired**, as in spec 005. Unchanged and untouched here.
