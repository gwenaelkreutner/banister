# Implementation Plan: intervals.icu as Sole Training Data Source

**Branch**: `002-intervals-icu-provider` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-intervals-icu-provider/spec.md`

## Summary

Replace Strava with intervals.icu as the single mandatory training data source: a personal API key instead
of OAuth, a five-minute poll instead of an inbound webhook, and source-computed metrics consumed rather
than recomputed.

The approach is **build alongside, then switch, then remove**. The intervals.icu client, its ingestion
path and its polling loop are built while Strava still works, so each can be exercised in isolation before
anything depends on it. Only once the new path produces a correct post-activity notification end to end
does the trigger move over — and only after that does the Strava code get deleted. At no point is the
system left with neither path working.

The existing three-layer separation (fetch → analyse → match) is what makes this tractable: `matching.py`
and `highlight.py` need no changes at all, and `SessionAnalyzer` is reduced rather than replaced. See
[research.md](./research.md) R6.

**One open question blocks part of Phase B** and is stated plainly rather than assumed away: six quality
metrics fall in neither the spec's "consume" list nor its "retain" list. See the Open Questions section.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: httpx (already present, used by the Strava client), SQLAlchemy 2.0 async +
aiosqlite, Alembic, aiogram v3, FastAPI. No new runtime dependency is anticipated.

**Storage**: SQLite (spec 003, complete). New tables/columns arrive through Alembic revisions — the
mechanism spec 003 established exists precisely so this feature does not hand-write schema changes.

**Testing**: pytest 8.3 with pytest-asyncio. Current suite: 190 passing, 14 skipped (the retired
PostgreSQL half of the portability tests).

**Target Platform**: Self-hosted container; no inbound network path required (spec 001 FR-002).

**Project Type**: Single-process web service plus Telegram bot.

**Performance Goals**: Steady-state polling must stay well inside the source's published quota — ~6% of
the daily allowance at the mandated five-minute interval (research R4). No throughput target otherwise.

**Constraints**: One athlete. No inbound endpoint. Credential encrypted at rest, with startup refusing
rather than degrading (FR-004). Exactly-once notification must survive restarts (FR-009).

**Scale/Scope**: 11 files under `app/strava/` to replace or retire; 24 further files referencing Strava;
~400 of `session_log.py`'s 775 lines removed with manual entry.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment | Verdict |
|---|---|---|
| **I. Deterministic engine, zero LLM load calculation** | Load stops being calculated at all — it is consumed from the source. The language model's role is unchanged and it still computes nothing. | ✅ Pass — strengthened |
| **II. Single-user, local-first, no cloud dependency** | Replaces an OAuth integration requiring a public callback with a personal key requiring nothing inbound. This is the change that makes spec 001's deployment story real. | ✅ Pass — advances it |
| **III. Clean layered architecture** | The fetch/analyse/match separation is what makes this migration tractable at all. FR-038 through FR-041 additionally *repair* existing violations (direct data access in a handler, context assembly inside the bot layer). | ✅ Pass — repairs it |
| **IV. Explicit data provenance, never estimate silently** | The core of this feature. FR-020's "unknown is not zero" governs every ingested metric, and FR-022 keeps per-activity values stable when thresholds change. Research R3 flags the one unknown that would violate it if got wrong. | ✅ Pass — central |
| **V. Engine logic is test-covered** | FR-034c requires it explicitly. Removing superseded calculations must also remove the tests covering only them, without weakening coverage of what remains. | ⚠️ See note |

**Note on Principle V**: `ruff check app/ tests/` currently reports 269 pre-existing violations. The
principle requires lint to pass, which no change can currently satisfy. As in spec 003, the working gate is
**no new violations against the recorded baseline**, with the backlog left to a separate effort.

**No violations requiring justification.** Complexity Tracking is therefore omitted.

## Project Structure

### Documentation (this feature)

```text
specs/002-intervals-icu-provider/
├── plan.md              # This file
├── research.md          # Phase 0 — API findings, and the unknowns that remain unknown
├── data-model.md        # Phase 1 — new storage, and the field mapping
├── quickstart.md        # Phase 1 — how to validate
├── contracts/
│   └── sport-provider.md  # Phase 1 — the boundary the rest of the app sees
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/
├── config.py                       # CHANGED: intervals settings become required; Strava's removed at cutover
├── providers/                      # NEW — replaces app/strava/ as the ingestion boundary
│   ├── intervals/
│   │   ├── client.py               #   HTTP: auth, activities, wellness, athlete, streams
│   │   ├── mapper.py               #   intervals.icu payload → AnalyzedSession
│   │   ├── poller.py               #   the five-minute loop, dedup, backlog bounding
│   │   └── errors.py               #   auth failure, rate limit, transient
│   └── analysis/                   # MOVED from app/strava/ — provider-independent
│       ├── analysis_models.py      #   RawActivity, AnalyzedSession (unchanged shape)
│       ├── analyzer.py             #   REDUCED: no TSS, no zone derivation; quality metrics kept
│       ├── matching.py             #   UNCHANGED (FR-033)
│       └── highlight.py            #   UNCHANGED
├── services/
│   └── activity_feedback.py        # NEW (FR-038): post-activity context assembly, no aiogram
├── db/
│   ├── models/
│   │   ├── wellness.py             #   NEW: dated recovery signals (FR-023)
│   │   ├── sync_state.py           #   NEW: reported markers + import progress (FR-009, FR-027)
│   │   └── oauth_connection.py     #   REMOVED at cutover — now genuinely obsolete
│   └── repositories/               #   +wellness_repo, +sync_state_repo; -oauth_repo
├── bot/routers/
│   ├── session_log.py              # REDUCED: manual entry removed, RPE capture kept (FR-031)
│   ├── strava.py                   # REMOVED
│   └── intervals.py                # NEW: connection status / reconnect guidance
├── main.py                         # CHANGED: poller task replaces the inbound webhook route
└── strava/                         # REMOVED in full at cutover
migrations/versions/                # NEW revisions: wellness, sync state, drop oauth_connections
tests/
├── test_providers/                 # NEW: client, mapper, poller, dedup
└── test_services/                  # NEW: activity feedback assembly
```

**Structure Decision**: `app/strava/` becomes `app/providers/`, splitting what was provider-specific from
what was never provider-specific. `matching.py`, `highlight.py` and `analysis_models.py` sit under
`analysis/` because they contain no Strava knowledge — the audit confirmed this and this migration proves
it, since they need no changes. Keeping them under a directory named for a provider we are removing would
be actively misleading.

## Implementation Phases

Each phase leaves the system working. Phases A–D add capability alongside Strava without changing observed
behaviour; E switches the trigger; F removes.

| Phase | Change | Strava still live? | Verified by |
|---|---|---|---|
| **A** | intervals.icu client + credential verification at startup | Yes | Real API call against a live key; startup refuses on a bad key |
| **B** | Payload → `AnalyzedSession` mapper; `SessionAnalyzer` reduced | Yes | Mapper tests against recorded fixtures; existing suite unchanged |
| **C** | Storage: wellness, sync state, reported markers | Yes | Alembic revision applies; dedup survives a restart |
| **D** | Poller loop + backlog bounding + history import | Yes (poller notifies nothing yet) | Poller detects a real activity; quota stays inside budget |
| **E** | Cutover: poller drives the post-activity notification | Being switched off | Full staged notification from a real ride, end to end |
| **F** | Removals: Strava, manual entry, `oauth_connections`; structural debt (FR-038..041) | No | Suite green; no Strava reference remains |

**Why this order**: the risky part is not the HTTP client, it is whether the notification loop still
behaves identically when driven by a poller instead of a webhook (FR-030). Everything before E exists to
make E a small, well-understood step rather than a leap. Deleting Strava before E would leave no working
path if E turned out harder than expected.

## Risks

| Risk | Mitigation | Requirement |
|---|---|---|
| A not-yet-computed load is stored as `0.0`, silently reading as a rest day | Confirm the null/absent convention against a live key **before** writing ingestion; assert it in a test | FR-020 |
| The same activity is notified twice, or missed, across a restart | Reported markers persisted, written only after delivery succeeds | FR-009, FR-012 |
| A long outage produces a burst of notifications | Backlog bounded explicitly; every activity still ingested, not every one announced | FR-011 |
| Post-activity notification quietly loses a stage when its trigger changes | Cut over in its own phase, verified against a real ride before Strava is removed | FR-030 |
| Quality metrics silently disappear | Open question resolved before Phase B, not discovered during it | See Open Questions |
| Threshold change retroactively alters stored history | Per-activity values written once at ingestion, never recomputed on read | FR-022 |
| Removing manual entry also removes RPE capture | They share an implementation; the split is an explicit task, not a side effect | FR-031 |

## Open Questions

**Questions 1 and 2 below were answered by verifying against the author's live account** — see
[research.md](./research.md) R9. Their original framing is kept because the *answers* changed the shape of
this feature, and a reader deserves to see why.

### ✅ Answered — and the answer was larger than the question

**The quality metrics.** R5 assumed intervals.icu did not provide them and recommended retaining all six.
Measurement showed otherwise: the payload carries 183 fields, and most of what `SessionAnalyzer` computes
already exists there. The revised split:

| Fate | Values |
|---|---|
| **Delete, consume theirs** | `variability_index` (verified numerically identical), `cardiac_drift_index` (→ `decoupling`), `dominant_zone`, `time_in_zones_s` (→ `icu_zone_times`, richer than ours) |
| **Keep — the source cannot know our plan** | `respect_zones_score`, `session_type_real` |
| **Keep, but rebuild on their interval detection** | `intervals_consistency_index` — they detect the intervals (`icu_intervals`, 9 with full metrics); the consistency score stays ours |

This **extends FR-017 well beyond what the spec anticipated**, and in the same direction: `app/engine/tss.py`'s
Banister-TRIMP HRSS implementation computes `hr_load` and `trimp`, both of which the source provides
directly. That removes more local computation than planned, not less.

**Consequence for Phase B**: no extra streams fetch is needed for the metrics after all — `decoupling`
comes on the activity payload. Streams are still available if `intervals_consistency_index` needs them.

### ✅ Answered — "how do we wait for the analysis to finish?"

We do not. There is no pending state. `analyzed` is always populated; `analysis_issues` is null across all
54 activities. A null `icu_training_load` means **the source cannot compute one** — no heart rate, no power
— and that is permanent, not transient.

It affects **8 of 54 real activities (15%)**. Storing those as `0.0` would record 15% of the athlete's
rides as rest days. FR-020 is therefore not a defensive nicety; it is load-bearing.

### ⚠️ Still open

1. **Which FTP the plan generator should use.** The athlete profile reports `icu_ftp: null`, while the
   activity carries `icu_ftp: 290`, `icu_pm_ftp: 225`, and `icu_rolling_ftp: 280`. Four answers, one
   question. Affects plan generation, not ingestion, so it does not block Phase A or B.

2. **The rate-limit response shape** (FR-013). Verifying it means deliberately exhausting the quota against
   a live account, which is not worth doing. Handle defensively and confirm from the response when it first
   occurs naturally.

3. **The existing `activities` table**, populated by the Strava historical import. Re-fetchable from the
   new source, so it can be truncated and repopulated — but that is a decision. Note that
   `compute_fitness_from_any()` duck-types across `SessionLog` and `Activity`; whatever is decided must not
   break that, or must remove the need for it.

4. **Wellness is empty.** Every one of `hrv`, `restingHR`, `sleepSecs`, `readiness` is unpopulated across
   all sampled days. Capture (FR-023) should still be built — it stores what exists. But **spec 006's
   readiness guardrails have no input**, and that is worth knowing before spec 006 is planned rather than
   during it.
