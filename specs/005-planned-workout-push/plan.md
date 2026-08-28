# Implementation Plan: Publishing Planned Sessions to the Athlete's Calendar

**Branch**: `005-planned-workout-push` | **Date**: 2026-08-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-planned-workout-push/spec.md`

## Summary

Publish approved, structured sessions to the athlete's intervals.icu calendar, from where their own head
unit collects them — gated on explicit per-publication approval, idempotent across republication, and
incapable of touching anything it did not write.

Phase 0 verified the whole write cycle against the real account ([research.md](./research.md)). Two
findings shape everything below:

1. **There is no upsert.** Re-POSTing the same `external_id` produces a *second* event, verified
   directly (R2). Idempotence (FR-014, FR-016, FR-017, SC-003) is entirely this system's job:
   read the window, match on `external_id`, `PUT` what exists, `POST` only what does not.
2. **Structured steps publish as a text DSL**, parsed server-side into `workout_doc` (R3) — and
   intervals.icu's computed duration for a repeat-group structure **exactly matched** spec 004's
   `derive_duration_minutes()` (R4). The two systems already agree on what a session is.

**This is the project's first outbound mutation.** Everything the athlete owns has been read-only until
now. Spec 001's FR-019 stops being a principle and becomes a code path here.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: httpx (already the client's transport), Pydantic v2, SQLAlchemy async, aiogram
v3 (approval interaction), pytest

**Storage**: SQLite via SQLAlchemy async. **This feature needs new tables** (unlike spec 004, which was
pure schema-in-JSON) — publication approvals and published entries must survive restarts to make
FR-005, FR-014 and FR-016 possible. New Alembic revision required.

**Testing**: pytest, plus **real-account verification** — write-testing was explicitly authorized by the
athlete. Unlike spec 004 (fully offline), the guarantees here are about a remote service's actual
behaviour, and R2 is exactly the kind of finding no mock would have produced.

**Target Platform**: Self-hosted single-process Linux container

**Project Type**: Single Python application

**Performance Goals**: Not a factor. A full horizon is ~10 requests against quotas of ~5000/day.

**Constraints**:
- Nothing is written without a recorded approval bound to the exact content shown (FR-001, FR-004, FR-005)
- Only `external_id`-prefixed entries are ever read for diffing, updated, or deleted (FR-015)
- Past-dated sessions are never written or rewritten (FR-011, FR-022)
- A session without steps is refused, never fabricated (FR-009) — the same rule spec 004 established
- Athlete edits and deletions are surfaced, not overwritten (FR-023, FR-024)

**Scale/Scope**: One athlete, a bounded horizon of the current + following week (spec assumption).

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Deterministic engine, zero LLM load calculation** | **PASS.** No load is computed here at all — and notably, intervals.icu computes `icu_training_load` itself from the published DSL (R4), so the published entry's load figure comes from the source, consistent with the metric-authority rule. The LLM's only role is conversational: the athlete may *ask* to publish; the LLM never decides what or whether to write. |
| **II. Single-user, local-first** | **PASS, with the sharpest test yet.** No new external service — it is the same intervals.icu account, same credential. But this is the first feature that *writes* to something the athlete owns, so "local-first" becomes "the athlete's remote data is theirs, and we are a guest in it" (the spec's own phrasing). FR-015 and FR-021 are the operative constraints. |
| **III. Clean layered architecture** | **PASS with a design obligation.** The split: DSL rendering and the events API are provider-specific and live in `app/providers/intervals/`; approval orchestration, plan↔calendar diffing and record-keeping are provider-agnostic and live in `app/services/`; the approval interaction lives in `app/bot/`. `app/engine/` is **not touched at all** — this feature reads sessions, it does not generate or modify them. |
| **IV. Explicit data provenance, never estimate silently** | **PASS, and load-bearing.** FR-009 (refuse a session with no steps rather than fabricate), FR-020 (say the calendar is out of date rather than let it look current), FR-026/FR-028 (report what failed rather than fail silently), and FR-006 (report what was *actually* written, not what was intended) are all direct applications. |
| **V. Engine logic is test-covered** | **PASS.** `app/engine/` is untouched, so the letter of this principle barely applies — but its spirit does, and the riskiest logic here (idempotent diffing, approval binding) gets the same treatment. |

### Finding: the constitution remains stale (unchanged since spec 004 raised it)

Recorded again because it is still true and still unaddressed — the constitution describes PostgreSQL,
Strava OAuth, `tests/test_strava/`, and manual `init.sql` migrations, none of which have existed since
specs 002/003. Amending it is a separately versioned act under its own Governance section, deliberately
not done as a side effect of a feature plan. Flagged in two consecutive plans now; worth doing on its own.

**Additionally relevant to this feature**: the constitution predates any outbound write and says nothing
about mutating athlete-owned remote data. The governing rule for that lives in spec 001 (FR-019, FR-019a),
not the constitution. A future amendment should probably promote it.

## Project Structure

### Documentation (this feature)

```text
specs/005-planned-workout-push/
├── plan.md              # This file
├── research.md          # Phase 0 output — live-verified API findings
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── calendar-publication.md
├── checklists/
│   └── requirements.md  # pre-existing
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/providers/intervals/
├── client.py                  # MODIFIED — _post/_put/_delete + list/create/update/delete_event
├── workout_dsl.py             # NEW — Step/RepeatGroup → intervals.icu DSL text (provider format)
└── calendar.py                # NEW — publish/withdraw against the events API, prefix-scoped

app/services/
└── publication.py             # NEW — approval lifecycle, plan↔calendar diff, divergence detection

app/bot/
├── routers/publish.py         # NEW — /publish, approval keyboard, withdrawal
└── keyboards/publish.py       # NEW — approve/decline inline keyboard (pub: prefix)

app/db/
├── models/publication.py      # NEW — PublicationApproval, PublishedEntry
└── repositories/publication_repo.py   # NEW

migrations/versions/           # NEW revision — two tables

tests/
├── test_providers/test_workout_dsl.py      # NEW — rendering, incl. against R3's verified output
├── test_providers/test_calendar.py         # NEW — prefix scoping, idempotent diff
├── test_services/test_publication.py       # NEW — approval binding, divergence, refusals
└── test_db/                                # extended — new tables round-trip
```

**Structure Decision**: Existing layout, no new top-level directory. The one judgement call is splitting
`workout_dsl.py` from `calendar.py`: the DSL is a pure, synchronous, fully-testable text transformation
with no I/O, while `calendar.py` is all I/O and ordering. Keeping them apart means the format — the part
most likely to need adjustment as real sessions hit real devices — is testable without touching the
network, and the R3 probe output can be used directly as a fixture.

## Complexity Tracking

No Constitution Check violations to justify.

## Phase boundaries and known scope limits

Recorded so they are decisions rather than discoveries:

1. **`banister:` prefix, deliberately distinct from enduragent's `cycling-coach:`** (research R5). The
   athlete's calendar already contains a real third-party entry from that tool, written with the same
   credential. Choosing a colliding prefix would make FR-015 unenforceable. As a bonus, SC-004's
   "calendar seeded with foreign entries" needs no seeding — it is already true.

2. **Approval is per-publication and content-bound, never standing** (spec assumption, FR-004). The
   approval records a hash of exactly what was shown; a plan change invalidates it rather than silently
   re-authorizing. This is what makes "the athlete approved *this*" answerable after the fact (FR-005).

3. **The horizon is current + following week** (spec assumption, FR-010), stated to the athlete rather
   than silent. Publishing a whole multi-month plan would fill their calendar with sessions certain to
   change.

4. **Device forwarding is the athlete's own setting** (FR-012). This system cannot enable it and must
   say so, or the first publication looks like it did nothing. This is a one-line product decision with
   an outsized effect on whether the feature appears to work at all.

5. **`fit_template()` stays unwired.** Spec 004 built it but left `generate_plan()` using its own budget
   math. This feature publishes whatever the plan already contains and does not change generation —
   explicitly out of scope per the spec ("Also excluded: changing how sessions are generated or
   structured"). The natural moment to wire fitting in is when a real device rejects a real session, not
   speculatively here.
