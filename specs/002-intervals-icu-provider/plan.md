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

## Open Questions — must be answered before implementation

1. **The six quality metrics** (`respect_zones_score`, `cardiac_drift_index`,
   `intervals_consistency_index`, `session_type_real`, `variability_index`, `dominant_zone`) are in
   neither FR-015's "consume" list nor FR-018's "retain" list. Research R5 recommends retaining all six —
   they are not load calculations, the source does not provide them, and the post-ride narrative depends
   on them throughout. Retaining the two stream-derived ones costs an extra API call per activity.
   **This needs the author's confirmation, and FR-018 amended to say so.**

2. **Five API behaviours remain unverified** because no key is configured (research R3, R5). The most
   consequential is the null-versus-zero convention for an incomplete load. These become verification
   tasks at the start of Phase A, not assumptions carried into Phase B.

3. **What happens to the existing `activities` table** — populated by the Strava historical import. Spec
   003 deliberately deferred touching it. It is re-fetchable from the new source, so it can be truncated
   and repopulated, but that is a decision, not an obvious default.
