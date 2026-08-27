# Contract: The Sport Provider Boundary

**Feature**: 002-intervals-icu-provider | **Date**: 2026-08-27

This feature exposes no external interface. Its contract is internal, and unlike spec 003's — which was
about *not moving* a boundary — this one is about **proving a boundary was real all along**.

The project's architecture claims that ingestion is decoupled from the business logic. Swapping the
provider is the first genuine test of that claim. If it holds, matching, scoring and notification content
need no changes. If it does not, that is a finding worth surfacing rather than papering over.

---

## What must not change

### `AnalyzedSession` — the handover type

Everything downstream consumes this. Its shape is the contract between "how we got the data" and
"what we do with it".

The **field set stays**, with the provenance shifts recorded in [data-model.md](./data-model.md). What must
not happen is downstream code learning where the data came from — no consumer should acquire a reference
to intervals.icu, just as none should have had one to Strava.

### Modules that must need zero changes

If these require edits, the layering was weaker than believed and that should be reported:

| Module | Why it should be untouched |
|---|---|
| `matching.py` | Scores an `AnalyzedSession` against a `SessionSpec`. Knows nothing about providers. |
| `highlight.py` | Selects a notable aspect and detects personal bests from stored logs. |
| `app/engine/*` | Periodization, plan generation, adherence, projection — never touched a provider. |
| `app/llm/*` | Narrates values it is handed. |

`analysis_models.py`, `matching.py` and `highlight.py` move directory (out of `app/strava/`, into
`app/providers/analysis/`) — but that is a rename, not a change. A move with edits is a signal.

### Behavioural guarantees carried over

Each is a spec requirement, and each is at risk during a provider swap precisely because it is
*incidental* to the change rather than the point of it:

1. **The staged post-activity presentation survives intact** — its ordering, its notable-aspect selection,
   its personal-best detection, and its restraint in raising only one alert (FR-030). The trigger changes;
   the athlete's experience must not.
2. **Perceived-exertion capture survives the removal of manual entry** (FR-031). They share an
   implementation today; only one is being removed.
3. **Feedback is still delivered when the athlete never answers** (FR-032).
4. **Matching behaviour is unchanged**: ±2 days bounded by the training week, a hundred-point score,
   no two activities claiming the same planned session, bonus framing when every slot is taken (FR-033).
5. **Unplanned training is never framed as failure** (FR-034).
6. **The weekly review and the daily reminder keep working** (FR-034a, FR-034b) — the weekly review is
   *not* unaffected: it consumes load values whose origin changes.

---

## What the provider boundary must provide

Stated as capability rather than signature, so the shape stays an implementation decision:

| Capability | Requirement |
|---|---|
| Verify the credential and identify the athlete | FR-002 — at startup, before anything else runs |
| List activities within a date window | FR-006 — the basis of periodic detection |
| Fetch one activity in full | Enough detail to populate `AnalyzedSession` |
| Fetch wellness records for a date range | FR-023 |
| Distinguish failure kinds | FR-005 (credential no longer accepted), FR-013 (transient vs. rate-limited) |

**Failure classification is part of the contract, not an implementation detail.** A caller must be able to
tell "your key was revoked" (tell the athlete, stop retrying) from "briefly unavailable" (retry quietly)
from "rate limited" (back off). Collapsing these into one exception type makes FR-005 and FR-013
unimplementable above this boundary.

---

## What callers must NOT start doing

The migration creates three temptations:

- **Recomputing a metric the source supplied.** FR-016 forbids it. If a value looks wrong, the fix is at
  the source or in the mapping — never a local recalculation, which would reintroduce exactly the
  divergence between the coach's numbers and the athlete's that spec 001 exists to prevent.
- **Treating a missing value as zero.** FR-020 and FR-024. Most affected columns are nullable floats
  feeding chronic load; a `None` silently becoming `0.0` reads as a rest day and corrupts every downstream
  figure without raising anything.
- **Reaching into the provider from a handler.** The audit already found direct data access inside an
  event handler once (FR-039). The new poller must not become a second instance.

---

## Verification

The contract is satisfied when:

1. `matching.py`, `highlight.py` and everything under `app/engine/` and `app/llm/` appear in the diff
   **only** as import-path updates from the directory move — no logic changes.
2. No module outside `app/providers/intervals/` references intervals.icu.
3. `grep -rn "strava\|Strava" app/` returns nothing after Phase F.
4. The full staged post-activity exchange is demonstrated from a real ride, before Strava is removed —
   not after.
5. Every consumed metric is asserted, in a test, to remain `None` rather than `0.0` when the source omits
   it.
