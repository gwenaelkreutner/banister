# Implementation Plan: Local Embedded Database

**Branch**: `003-local-embedded-database` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-local-embedded-database/spec.md`

## Summary

Replace the hosted PostgreSQL database with a single-file SQLite database inside the athlete's data
directory, created and evolved automatically with no database administration.

The approach is **portability first, cutover last**. Three of the four risky changes — dialect-neutral
column types, dialect-neutral upsert, and UTC-preserving timestamps — are behaviour-preserving on
PostgreSQL and land while it is still the backend, where the existing test suite validates them against a
known-good baseline. Only after those are green does the engine change, at which point the remaining
delta is the connection layer and the pragmas that make SQLite behave like the database the application
was written against.

Three defaults of the target engine are treated as first-class risks rather than details, because each was
empirically confirmed to fail silently rather than loudly: foreign keys are not enforced, timezone offsets
are discarded, and concurrent writers are serialised. See [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: SQLAlchemy 2.0.48 (async), Alembic 1.18.4 (already a dependency, currently
unused), aiosqlite (to add), aiogram v3, FastAPI. `asyncpg` is removed at cutover.

**Storage**: SQLite 3.50.4, single file under the athlete's data directory, WAL journalling

**Testing**: pytest 8.3 with pytest-asyncio; the existing suite (164 tests) is the regression baseline

**Target Platform**: Linux container on a self-hosted VPS; also runs directly on the developer's machine

**Project Type**: Single-process web service plus Telegram bot; internal layered architecture

**Performance Goals**: Modest by design — one athlete, years of history, not millions of rows. Routine
operations must stay responsive against a multi-year store (SC-008); no throughput target applies.

**Constraints**: No separately administered database service. No manual schema step. All athlete data
beneath one deletable directory. Several concurrent background schedulers plus interactive handlers share
one writer.

**Scale/Scope**: One athlete per deployment. 8 tables, 8 repositories, ~3200 lines of engine code that
reads through them.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment | Verdict |
|---|---|---|
| **I. Deterministic engine, zero LLM load calculation** | This feature touches persistence only. No calculation moves, and no LLM call is introduced anywhere in the data layer. | ✅ Pass |
| **II. Single-user, local-first, no cloud dependency** | This feature is the direct implementation of the principle: it removes the last mandatory cloud service. Credentials for the removed database leave configuration. | ✅ Pass — advances it |
| **III. Clean layered architecture** | Changes are confined to `app/db/`. Repositories keep their signatures, so no handler or engine module changes. The port must not become an excuse to introduce data access elsewhere. | ✅ Pass |
| **IV. Explicit data provenance, never estimate silently** | FR-011 carries the rule into storage: unknown must remain distinguishable from zero, false, and empty. R2 is a provenance risk in disguise — a dropped timezone makes a stored instant silently mean something else. | ✅ Pass — reinforced |
| **V. Engine logic is test-covered** | Engine code is not modified, but every engine module reads through this layer. The port is validated by the existing 164-test suite plus new tests for the three silent-failure modes. `ruff` and `pytest` must pass at each phase boundary. | ⚠️ See note |

**Note on Principle V**: the repository currently reports 291 `ruff` violations, all pre-existing. The
principle requires lint to pass before a change is considered done, which no change can currently satisfy.
This is recorded as a known blocker rather than silently ignored or fixed inside this feature. The
pragmatic gate adopted here is **no new violations introduced**, with the backlog addressed separately.

**No violations requiring justification.** The Complexity Tracking section is therefore omitted.

## Project Structure

### Documentation (this feature)

```text
specs/003-local-embedded-database/
├── plan.md              # This file
├── research.md          # Phase 0 output — empirically verified findings
├── data-model.md        # Phase 1 output — entities and their portability deltas
├── quickstart.md        # Phase 1 output — how to validate the migration
├── contracts/
│   └── persistence.md   # Phase 1 output — the internal contract that must not change
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
app/
├── config.py                    # CHANGED: database URL becomes a data-directory path
├── db/
│   ├── client.py                # CHANGED: engine creation, connection pragmas, pooling
│   ├── types.py                 # NEW: UtcDateTime decorator (R2)
│   ├── lifecycle.py             # NEW: startup — migrations, version guard, instance lock
│   ├── models/                  # CHANGED: portable column types across all 8 models
│   │   ├── base.py              #   server_default timestamps → portable form
│   │   ├── user.py  profile.py  training_plan.py  session_log.py
│   │   ├── chat_message.py  activity.py  weekly_adherence.py
│   │   └── oauth_connection.py  #   removed at cutover (belongs to the removed provider)
│   └── repositories/            # CHANGED: 3 files only, for dialect-neutral upsert
│       ├── activity_repo.py     #   on_conflict_do_nothing
│       ├── oauth_repo.py        #   on_conflict_do_update
│       └── weekly_adherence_repo.py  # on_conflict_do_update
├── main.py                      # CHANGED: lifespan acquires instance lock, runs migrations
migrations/
├── init.sql                     # REMOVED at cutover, replaced by:
└── versions/                    # NEW: Alembic revisions, baseline == current schema
scripts/
├── carry_over.py                # NEW: one-time PostgreSQL → SQLite for local-origin data
└── backup.py                    # NEW: VACUUM INTO snapshot + documented restore
tests/
└── test_db/                     # NEW: the three silent-failure modes, plus lifecycle
docker-compose.yml               # CHANGED: postgres service removed; volume becomes data dir
```

**Structure Decision**: The existing layout is kept unchanged. This is a storage-engine port, not a
restructuring, and the spec explicitly excludes schema redesign. Everything above `app/db/` is untouched
by design — that is the property that makes the change reviewable, and the contract in
`contracts/persistence.md` states it as an invariant rather than an aspiration.

## Implementation Phases

Each phase is independently verifiable and independently revertible. Phases A–C run on PostgreSQL, so the
existing suite validates them against a known-good baseline before the engine ever changes.

| Phase | Change | Backend during | Verified by |
|---|---|---|---|
| **A** | Portable column types: `Uuid`, `JSON`, `UtcDateTime` | PostgreSQL | Existing 164 tests still pass; new timestamp round-trip test |
| **B** | Dialect-neutral upsert in 3 repositories | PostgreSQL | Existing tests; upsert behaviour test |
| **C** | Adopt Alembic; baseline revision equals current schema | PostgreSQL | Fresh database from migrations matches `init.sql` output |
| **D** | Engine, pragmas, pooling, instance lock, version guard | SQLite | New `tests/test_db/`; full suite on SQLite |
| **E** | Carry-over script for local-origin data | Both | Round-trip on a copy; coach answers match pre-move |
| **F** | Cutover: remove `asyncpg`, `init.sql`, postgres service | SQLite | Full suite; fresh-deployment quickstart |

The ordering exists because a failure in phase A on PostgreSQL is a type bug, whereas the same failure
discovered after the engine change is ambiguous between a type bug and an engine difference. Separating
them keeps every failure attributable.

## Risks

| Risk | Mitigation | Requirement |
|---|---|---|
| Cascade deletes silently stop working | Pragma on every connection, asserted by a test that deletes a parent and counts orphans | FR-006 |
| Timestamps silently change meaning | UTC-normalising type decorator; test asserts offset survives round-trip | FR-010 |
| "Database is locked" surfaces to the athlete | WAL plus busy timeout; concurrency test drives schedulers and handlers together | FR-013, FR-014 |
| A metric that was unknown becomes zero | Round-trip test asserting `None`, `0`, `False` and `{}` stay distinct | FR-011 |
| Pool class wrong for the async driver | Chosen then measured under concurrent load, not assumed | FR-013 |
| Carry-over loses irreplaceable data | Runs against a copy; source left intact and retryable; activities re-fetched rather than moved | FR-025 |

## Open items for `/speckit-tasks`

1. Connection pool class for the async SQLite driver — decide, then verify under concurrent load (R5).
2. Whether the instance lock lives in the data directory or beside the database file (R9).
3. Whether `oauth_connection` is dropped in phase C's baseline or at phase F's cutover — it belongs to the
   provider integration being removed by spec 002, which is built after this one, so it must survive the
   port and be dropped later.
