# Data Model: Training Guardrails

**Feature**: 006-training-guardrails | **Date**: 2026-08-28

Entities from [spec.md](./spec.md) §Key Entities, resolved against what the live data actually contains
([research.md](./research.md)).

**The organising rule**: a guardrail finding is **derived on demand and never stored**. A stored finding is
a cache that goes stale exactly when it matters — after the day it describes has passed, or after the
athlete has already acted on it. Only two things here need to survive a restart, and both are records of
something that *happened* rather than something that *is*: a check that failed (FR-021) and a
recommendation the athlete declined (FR-025). This mirrors spec 005's treatment of divergence.

---

## Derived, not stored

### Workload signals

| Signal | Source | Why not computed locally |
|---|---|---|
| **Acute:chronic ratio** | `wellness.atl / wellness.ctl` | research R3 — the local load series double-counts by ~1.8×; the source's figure is authoritative (Principle IV), de-duplicated by construction, and present on rest days |
| **Ramp rate** | `wellness.ramp_rate` (new column) | research R4 — the source computes CTL gain per week itself; consumed as-is |
| **Load uniformity** | `weekly_snapshot.monotony_index`, **corrected** | research R2 — the shipping value drops rest days and is biased toward false alarms |

`ATL/CTL` is a 7d:42d exponentially-weighted ratio, **not** Gabbett's rolling 7:28. The documented range
belongs to the EWMA form and is published as such (FR-016) — see `guardrail_thresholds.py` below.

### Recovery baseline

The athlete's own rolling normal for one signal, computed from `wellness` rows and **never stored**:
recomputing is cheap, and a stored baseline cannot satisfy FR-015 ("baselines MUST adapt") without an
invalidation rule that would itself be a source of staleness.

- Window and minimum-sample requirements live in `guardrail_thresholds.py`, not in the function body.
- Computed from the athlete's own history only — never a population value (FR-006, US5 acceptance 3).
- Below the minimum sample count the baseline is **`None`, not a default** (FR-013, FR-014).

**A baseline and a current observation are independent.** Research R1 found this athlete has a complete
June/July resting-HR baseline and no reading since 19 July. The evaluator must be able to hold *"baseline
known, today unknown"* and report exactly that. Collapsing the two — reusing the baseline, or the last
known value, as today's observation — reads six-week-old data as current and is the specific defect FR-014
forbids.

### Recovery signal

A dated observation of one wellness metric, evaluated against its baseline. Read from the existing
`wellness` table; this feature adds no capture. Every field there is already nullable precisely so that a
missing reading cannot be mistaken for a normal one (that table's own docstring anticipates this feature).

### Guardrail finding

What an evaluation produces. Never persisted; carried in memory to the narration layer.

| Field | Rule |
|---|---|
| `kind` | which guardrail fired |
| `observed` | the value measured |
| `reference` | the baseline or range it was measured against |
| `threshold` | the documented value that was crossed |
| `action` | what the athlete can do — **mandatory** (FR-027, SC-003): a finding with no action is not constructible |
| `severity` | ordering only, so simultaneous findings can be ranked rather than dumped |
| `occurrence_key` | stable identity for "this same occurrence", so a decline can suppress it (FR-025) |

`action` being a required field rather than an optional one is deliberate: SC-003 requires *every* finding
to carry a recommended action, and a type that cannot represent an actionless finding enforces that at
construction rather than at review.

### Response check

The verification applied to a coaching response before delivery (FR-019). Derived per response; only its
**failures** are persisted (below).

- **MetricRegistry** — `{metric_name: value}`, assembled while the context is built, recording exactly
  what was put in front of the model. It is the definition of "retrieved" for FR-018 and FR-020: a metric
  absent from the registry is one the response may not state a value for.
- Verification is **keyword-anchored** (research R5): a number is a claim only when adjacent to a metric
  term. Tolerance is explicit, so display rounding (prompt 45.9 → response "46") passes.

---

## Persisted

### `wellness.ramp_rate` — one new column

`Float`, nullable, on the existing table. The source's own CTL-gain-per-week (research R4), populated by
`ingest_wellness` alongside `ctl`/`atl`. Nullable for the same reason every other column there is: absent
must stay distinguishable from zero. One Alembic revision.

### `ResponseCheckFailure` — FR-021, SC-001, SC-002

Recorded so the frequency of failures is **measurable**, which is what SC-001 and SC-002 are stated in
terms of. Without a table those success criteria can only be asserted.

| Column | Type | Rule |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | FK → `users.id`, cascade | |
| `failure_kind` | `String(32)` | `"mismatch"` (stated value ≠ retrieved) \| `"unretrieved"` (value stated for a metric never retrieved, FR-018) |
| `metric_name` | `String(64)` | which metric the claim was anchored to |
| `stated_value` | `String(64)` | what the response said — text, not numeric: the point is to keep the claim verbatim |
| `expected_value` | `String(64) \| None` | what the registry held; `None` for `"unretrieved"` |
| `response_excerpt` | `Text` | the sentence containing the claim, so a failure is diagnosable without replaying the conversation |
| `occurred_at` | `UtcDateTime` | |

Kept indefinitely and never pruned by this feature: the measurement is over a corpus, and a table that
silently forgets its failures cannot support "verified across a representative corpus" (SC-001).

### `GuardrailAcknowledgement` — FR-025, FR-026

The record that the athlete was shown a finding and declined it. Needed because FR-025 forbids raising the
same occurrence repeatedly, and "same occurrence" is not answerable from derived state alone.

| Column | Type | Rule |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | FK → `users.id`, cascade | |
| `finding_kind` | `String(32)` | which guardrail |
| `occurrence_key` | `String(128)` | what makes this the *same* occurrence — see below |
| `decision` | `String(16)` | `"accepted"` \| `"declined"` |
| `decided_at` | `UtcDateTime` | |

**`occurrence_key` is the whole design.** Too coarse (`kind` alone) and one decline silences the guardrail
forever, which FR-026 forbids — the signal must keep being stated. Too fine (`kind + timestamp`) and every
re-evaluation is a new occurrence, so the athlete is nagged, which FR-025 forbids. The key is
`kind + the day the finding is about`, so declining today's finding settles today and tomorrow's evaluation
is genuinely new.

**FR-026 is a presentation rule, not a storage one**: a declined occurrence stops being *raised as a
recommendation*, but the underlying signal continues to appear in the coach's context. The athlete is
never obstructed and never nagged — they are simply told what is true and left to decide, which is the
distinction US4 exists to draw.

---

## Not a table: the thresholds

`app/engine/guardrail_thresholds.py` — every threshold and range in one inspectable module, each with its
published source named next to it (FR-016, SC-007: "can be located by a reader without reading code" is
satisfied by a module that reads as documentation).

Covers: the EWMA acute:chronic range, the ramp-rate caution band, the monotony threshold, HRV −20%,
resting HR +5 bpm, baseline windows and minimum sample counts, the outlier guard, and the verification
tolerance.

They are **configurable defaults, not constants** (spec Assumptions): named, documented, and changeable in
one place — but changing one is a deliberate act, which is exactly what putting them in a module read as
documentation rather than scattered through call sites achieves.

## What this feature does not model

`SessionSpec`, the plan, and the calendar are untouched. Guardrails advise; an accepted recommendation is
applied through the **existing** modification and publication approval paths (FR-024) — spec 004's
`plan_modifier` and spec 005's `authorize_publication`. This feature adds no write path of its own, which
is what makes FR-023 and SC-006 true by construction rather than by inspection.
