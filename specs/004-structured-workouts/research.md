# Research: Structured Workouts and Session Library

**Feature**: 004-structured-workouts | **Date**: 2026-08-27

Phase 0 output. Each item below was an unknown that would otherwise have been guessed at during
implementation. Findings come from reading the running code, not from assumption.

---

## R1 — The structure is already computed, then discarded

**Question**: How far is the generator from being able to emit steps?

**Finding**: Much closer than the spec assumes. `app/engine/plan_builder.py` already holds interval
structures as data:

```python
THRESHOLD_STRUCTURES = [
    (3, 12, 4, "3×12min"),   # sets, work_min, rest_min, label
    (2, 20, 5, "2×20min"),
    ...
]
WARMUP_MIN = 15; WARMUP_VO2_MIN = 20; COOLDOWN_MIN = 15
```

and `_structure_duration()` is literally the step-sum:

```python
return max(40, min(120, warmup_min + sets * work + (sets - 1) * rest + COOLDOWN_MIN))
```

The generator therefore *knows* warm-up, work interval, recovery interval, repetition count and
cool-down for every interval session. It then collapses all of it into a flat `SessionSpec` plus a
French label string (`"3×12min"`), and the structure is unrecoverable downstream.

**Decision**: Frame this work as **stop discarding structure that already exists**, plus move the
hardcoded tuples into a data-driven library — not as inventing a structural model from nothing.

**Consequence**: US1 (steps) and US3 (library) are much less risky than their spec text suggests. The
real risk concentrates in US2 (not breaking ~14 construction sites and ~20 readers) and US5 (stored
plans), which is where the task budget should go.

**Alternatives considered**: Designing a step model independently of the existing tuples, then
retrofitting. Rejected — the existing tuples encode reviewed training content (progressive sweet-spot,
threshold and VO2 structures); redesigning around them risks silently changing prescribed training,
which this spec explicitly forbids (periodization and load are out of scope).

---

## R2 — FR-005 is already violated, latently

**Question**: Can a session's stated duration disagree with the sum of its steps today?

**Finding**: Yes, structurally — `_structure_duration()` clamps to `max(40, min(120, ...))`. When the
clamp bites, `duration_minutes` is no longer the sum of the parts.

Checked against every structure currently in the file, the clamp does **not** currently bite:

| Structure | warmup + work + rest + cooldown | Clamped? |
|---|---|---|
| Sweet spot 3×15r5 | 15 + 45 + 10 + 15 = 85 | no |
| Threshold 2×20r5 | 15 + 40 + 5 + 15 = 75 | no |
| Threshold 3×15r5 | 15 + 45 + 10 + 15 = 85 | no |
| VO2 6×5r3 | 20 + 30 + 15 + 15 = 80 | no |

So the defect is latent, not active: no plan generated today is inconsistent, but any contributor
adding a longer template to the library would silently produce one.

**Decision**: Make duration a **derived property** of the steps (FR-005), and move the clamp from a
silent output-mangler to an input constraint that **refuses and reports** (FR-024). A template whose
fitted duration exceeds the athlete's availability must raise, not quietly shrink.

**Alternatives considered**: Keeping the clamp and letting duration drift from the steps. Rejected —
that is exactly the drift FR-005 and FR-009 exist to prevent, and it would make the derived-summary
guarantee untestable.

---

## R3 — Section 11's library is not in this repository

**Question**: Where does the library's content come from?

**Finding**: `Section 11` appears **only** in `specs/004-structured-workouts/spec.md`. It is not
vendored, not in `docs/`, not in any dependency. The spec calls it "the intended starting point",
MIT-licensed, whose "structure is adopted; its training content is reviewed rather than copied
blindly."

Separately: the repository currently has **no `LICENSE` file and no attribution file** (checked). The
open-source-release requirements of spec 001 (FR-024, FR-026, FR-028) are unmet, and `.github/workflows/ci.yml`
is wired to a `main` branch while the repository is on `master`, so CI has never run.

**Decision**: Build the library from the **structures already in `plan_builder.py`**, which are the
project's own reviewed training content, and treat Section 11 as a review reference rather than an
import. This removes the licence-compliance blocker from this feature's critical path entirely: if no
Section 11 material is copied, no attribution obligation is triggered by this work.

**If Section 11 material is later copied verbatim**, a `NOTICE` file carrying its MIT licence and
attribution MUST land in the same change (spec 001 FR-028). That obligation is recorded as a task
precondition rather than left implicit.

**Alternatives considered**: Fetching and vendoring the Section 11 library up front. Rejected for now —
it makes an unmet licence-hygiene requirement (no `LICENSE`, no `NOTICE`) block a feature that does not
otherwise need it, and the existing structures already cover the periodization's full vocabulary (R4).

---

## R4 — Library coverage is bounded and small

**Question**: How many templates does FR-019 / SC-007 ("every session type the periodization can
request, across every phase") actually require?

**Finding**: The session-type vocabulary is narrow. `SessionSpec.workout_type` is a 4-value `Literal`:
`long_ride | intervals | endurance | recovery`. Intensity variety lives in the *interval detail*, not in
the type. `app/engine/periodization.py` emits only `(phase, tss_target, is_recovery_week)` — it carries
no session vocabulary at all. Composition happens entirely in `plan_builder._build_week_template()`.

So the required coverage is:

| Family | Variants today | Zone |
|---|---|---|
| long_ride | 1 (duration scales with volume/phase) | Z2 |
| endurance | 1 | Z2 |
| recovery | 1 | Z1 |
| intervals — sweet spot | 3 | Z3 |
| intervals — threshold | 4 | Z4 |
| intervals — VO2 | 3 | Z5 |
| race week | 3 special sessions (`_build_race_week`) | mixed |

**Decision**: A library of roughly 15 templates covers the entire current vocabulary. Coverage is
therefore *provable by test* (SC-007) rather than aspirational — a test can enumerate every
`(phase, workout_type)` the periodization can produce and assert the library answers each one.

---

## R5 — Relative intensity already has a home

**Question**: How should templates express intensity relative to thresholds (FR-022), and how does that
resolve for a heart-rate athlete (FR-026) or an athlete with no threshold (FR-027)?

**Finding**: `app/engine/schemas.py::Zone` already carries both halves:

```python
lower_pct: float; upper_pct: float          # relative — always present
lower_watts / upper_watts | lower_bpm / upper_bpm   # resolved — mode-dependent, nullable
```

and `app/engine/zones.py` resolves them per mode (`compute_power_zones(ftp)` /
`compute_hr_zones(hr_max, hr_rest)`, the latter via Karvonen reserve). `TrainingPlanSchema.zones` already
stores the resolved dict on the plan, and `coaching_mode` already discriminates power vs HR.

**Decision**: Templates express intensity as a **zone code** (`"Z4"`), never as watts or bpm. Resolution
to absolute targets happens at presentation time against `plan.zones`, which is already mode-aware. This
satisfies FR-022, FR-025 (threshold change → future sessions retarget, stored history untouched, because
nothing absolute is persisted on the step), FR-026 and FR-027 (no threshold → the zone code is still
presentable; absolutes are simply absent rather than fabricated, per Constitution Principle IV).

**Alternatives considered**: Storing `%FTP` on each step. Rejected — it duplicates what `Zone.lower_pct`
already holds and creates a second source of truth for the same number.

---

## R6 — Description localization has an unwired prerequisite

**Question**: What does it take to stop emitting French from the generator (FR-028/029/030)?

**Finding**: Two independent French-locked surfaces exist:

1. `SessionSpec.description_fr` — built by `plan_builder._session_description()` from hardcoded French
   strings, including a long RPE caveat for Z5/Z6 in HR mode.
2. `Zone.description_fr` — hardcoded French in `zones.py::_zone_description()`.

The voice mechanism to replace them exists and works — `app/core/persona.py::load_persona()` returns a
`Persona` carrying `language`, and four personas ship (`analyst`, `coach-default`, `pace`, `zen`) — but
`load_persona()` is **called from nowhere in the application**. This is the same "built, tested, never
wired" pattern found in spec 002 (`ingest_wellness`/`import_history`).

Spec 004 and spec 007 both name this wiring as a shared prerequisite and warn that doing it twice would
produce two different answers.

**Decision**: Scope this feature to making descriptions **derivable from structure** (FR-028) and
removing `description_fr` as the *only* description (FR-030) — that is, the session carries structure
plus an optional description, and a renderer produces text. Wiring `load_persona()` into the prompt layer
is left to spec 007, which owns coach voice, with this feature depending on the renderer contract rather
than on the persona being wired. This keeps the two specs from racing on the same wiring.

**Consequence**: FR-029 ("descriptions follow the configured language") is satisfiable by this feature
only to the extent that the renderer takes a language parameter. Full end-to-end language switching lands
with spec 007. This is recorded as a **scope boundary, not a gap** — flagged here rather than discovered
during implementation.

---

## R7 — The compatibility surface is larger than "twenty places"

**Question**: How many places actually read or construct a `SessionSpec`?

**Finding**, from a full grep of `app/` and `eval/`:

- **Construction sites**: ~7 in `plan_builder.py`, **7 in `plan_modifier.py`** (injury adaptation, week
  adjustment, session adjustment, progressive recovery, shifting). Every one must emit steps that agree
  with the summary (FR-012).
- **Read sites**: `plan_modifier` (heaviest), `plan_builder`, `providers/analysis/matching.py`,
  `engine/adherence_kpi.py`, `llm/activity_analysis.py`, `llm/tools.py`, `llm/prompts.py`,
  `llm/narrator.py`, `services/weekly_recap.py`, `services/activity_feedback.py`,
  `bot/routers/session_log.py`, `bot/routers/plan.py`, `engine/atl_ctl.py`, plus `eval/rules/checker.py`
  and `eval/llm/prompts.py`.

`plan_modifier.py` is the risk concentration: it both reads and reconstructs sessions, and it is driven
by an LLM tool, so its output is not fully predictable from tests alone.

**Decision**: Take the **additive** path the spec assumes (steps added; existing summary attributes
retained as values derived from steps). Confirmed viable because `eval/rules/checker.py` reads only
`zone_code`, `workout_type`, `target_time_in_zone_minutes` and `tss_target` — all of which the additive
approach preserves, so FR-014a/SC-005a hold without touching the eval framework.

**Additionally**: give `SessionSpec` a **single constructor path** that derives the summary from steps,
so the 14 construction sites cannot each re-derive it differently. Sites that today build a summary
directly become sites that build steps.

---

## R8 — Backward compatibility is a Pydantic default, not a migration

**Question**: How do plans stored under the previous shape keep working (FR-013, US5)?

**Finding**: Plans are stored as JSON in `training_plans.plan_technical` and re-validated through
`TrainingPlanSchema.model_validate()` on every read. There is no per-session database column, so there is
**no schema migration to write** — a stored session simply lacks the new key.

**Decision**: `steps` is optional (`list[Step] | None = None`). A session loaded without steps validates
successfully and keeps its stored summary attributes verbatim. Anything requiring steps must report
absence rather than fabricate them (FR-014) — which for a derived-summary design means the derivation is
applied **only when steps are present**, and the stored summary is trusted otherwise.

This also settles the "partially structured plan" edge case (some sessions with steps, some without): it
is the normal state during a transition and needs no special handling beyond per-session nullability.

**Alternatives considered**: A data migration rewriting stored plans to add steps. Rejected — it would
have to invent steps for sessions whose structure was already discarded (R1), i.e. fabricate exactly what
FR-014 forbids.

---

## Open questions carried into implementation

1. **Fitting arithmetic (FR-023)**: what "preserving structural character" means numerically — scaling
   repetition count, work duration, or both, and in what order — is a training-quality decision best
   settled against the eval framework's before/after comparison (SC-005a) rather than argued in advance.
   The eval harness exists and runs, so this is measurable rather than speculative.
2. **Deterministic tie-breaking (FR-020)**: the current code rotates structures by `week_in_block %
   len(STRUCTURES)`, which is already deterministic and repeatable. Whether the library keeps that exact
   rotation or adopts an explicit ordering key is an implementation choice with no behavioural difference
   for existing plans, and is resolved by keeping rotation unless a test demands otherwise.
