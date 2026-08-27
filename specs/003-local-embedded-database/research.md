# Phase 0 Research: Local Embedded Database

**Feature**: 003-local-embedded-database | **Date**: 2026-08-27

All findings below were verified empirically against SQLAlchemy 2.0.48 and SQLite 3.50.4 on this machine,
not taken from documentation or recollection. The reproduction script is described in `quickstart.md`.

---

## R1. Foreign key enforcement is off by default, and fails silently

**Decision**: Enable `PRAGMA foreign_keys=ON` on every connection via a connection-level event hook, and
assert it is on in a test.

**Evidence**: On a fresh connection `PRAGMA foreign_keys` returns `0`. With cascade-declared models,
deleting a parent left its child row in place — the delete reported success and the orphan persisted.
Adding the pragma on connect made the same delete cascade correctly.

```
foreign_keys default    : 0
orphans without pragma  : 1   ← CASCADE silently ignored
orphans with pragma     : 0   ← CASCADE honoured
```

**Why this matters here**: every model in this project declares `ondelete="CASCADE"`. Without the pragma
the declarations become decorative and orphan rows accumulate invisibly. This is the single highest
silent-corruption risk in the migration and satisfies FR-006.

**Alternatives rejected**: setting the pragma once at startup (it is per-connection, so pooled connections
would not get it); relying on ORM-level cascade alone (does not cover deletes issued outside the ORM
session, and diverges from the declared schema).

---

## R2. Timezone information is lost on read-back

**Decision**: Introduce a type decorator that normalises to UTC on write and re-attaches UTC on read, and
use it wherever `DateTime(timezone=True)` is used today.

**Evidence**: A value written as `2026-08-27 10:00+00:00` came back as `2026-08-27 10:00` with
`tzinfo=None`. SQLite has no timezone-aware type; the offset is discarded.

**Why this matters here**: naive datetimes compare and subtract silently but wrongly. Daily boundaries
drive the session reminder, weekly aggregation, and the ±2-day activity-to-session matching window, so a
dropped offset shifts training days rather than raising an error. This satisfies FR-010.

**Alternatives rejected**: storing naive UTC and remembering to treat it as UTC everywhere (relies on
discipline at every call site, and the codebase already mixes `utcnow()` with timezone-aware values);
storing epoch integers (loses readability and breaks existing date comparisons in queries).

---

## R3. Portable column types exist and cover current usage

**Decision**: Replace `postgresql.UUID` with `sqlalchemy.Uuid(as_uuid=True)` and `postgresql.JSONB` with
`sqlalchemy.JSON`. Both are dialect-neutral in SQLAlchemy 2.0 and render appropriately per backend.

**Evidence**: `Uuid(as_uuid=True)` renders as `CHAR(32)` on SQLite while still returning `uuid.UUID`
objects in Python. A JSON document containing `None`, an empty object, an empty list, nested objects and
booleans round-tripped identically.

**Why this matters here**: this is what makes the port additive rather than a rewrite — the same model
definitions serve both backends, so the type change can land and be verified while still running on
PostgreSQL. Satisfies FR-007 and FR-012.

**Alternatives rejected**: keeping dialect-specific types behind a conditional (two code paths to test);
storing UUIDs as strings manually (loses type safety at the Python boundary).

---

## R4. Upsert is available on the target dialect

**Decision**: Import the `insert` construct from the active dialect rather than hard-coding the PostgreSQL
one. Three repositories use `on_conflict_do_nothing` / `on_conflict_do_update`.

**Evidence**: `sqlalchemy.dialects.sqlite.insert(...).on_conflict_do_update(...)` compiles to valid
`ON CONFLICT ... DO UPDATE` SQL. SQLite has supported this since 3.24; the installed version is 3.50.4.

**Why this matters here**: satisfies FR-009 without changing repository call sites' behaviour.

**Alternatives rejected**: emulating upsert with select-then-insert-or-update (introduces a race the
database already solves); `INSERT OR REPLACE` (deletes and reinserts the row, which would fire cascades
and lose unspecified columns).

---

## R5. Concurrency: write-ahead logging plus a busy timeout

**Decision**: Set `journal_mode=WAL` and a `busy_timeout` on connect, and keep writes short.

**Rationale**: The application runs several concurrent background activities — the weekly review
scheduler, the session reminder scheduler, and soon the activity refresh — alongside conversation
handling. SQLite serialises writers. WAL lets readers proceed during a write instead of blocking, and
`busy_timeout` makes a blocked writer wait and retry inside the driver rather than raising
"database is locked" up to the athlete. Together these satisfy FR-013 and FR-014.

**Alternatives rejected**: application-level write locking (reimplements what the database provides, and
does not protect against a second process); serialising all database access through a single task (turns
every read into a queue entry and couples unrelated features).

**Open item for implementation**: the connection pool class for the async SQLite driver should be chosen
and then verified under concurrent load rather than assumed. This is called out as a task rather than
settled here, because the right choice depends on file-versus-memory and on pool recycling behaviour that
is worth measuring.

---

## R6. Snapshot without stopping the system

**Decision**: Use SQLite's `VACUUM INTO` to produce a backup file.

**Rationale**: It writes a consistent snapshot of the live database to a new file while the database
remains in use, which is exactly FR-016's requirement of a complete consistent snapshot without stopping.
Restoring is replacing the file. Available since SQLite 3.27; installed version is 3.50.4.

**Alternatives rejected**: copying the database file with `cp` (can capture a torn write, and misses the
WAL contents); dumping to SQL text (slower, larger, and lossy for exact numeric representations).

---

## R7. Schema evolution: adopt the migration tool already declared

**Decision**: Use Alembic, generating a baseline migration equal to the current schema, and run migrations
automatically at startup.

**Evidence**: Alembic 1.18.4 is already installed and already listed as a project dependency — but there
are no migration scripts, and the schema is created by a single hand-maintained `migrations/init.sql`
executed by the container on first start. The dependency exists; the mechanism was never adopted.

**Why this matters here**: FR-018 requires schema changes to apply automatically with no operator action,
FR-019 requires idempotence, FR-021 requires refusing to run against a newer schema, and FR-022 requires
the storage to record its version. Alembic provides the version marker and the idempotent upgrade path
directly. Adopting it also removes the manual "paste this SQL into the hosted console" step that is
acceptable for one author and impossible for distributed self-hosters.

**Alternatives rejected**: continuing with hand-written SQL files and an ordering convention (no version
marker in the database, no idempotence, no downgrade path, and no protection against running old software
against a newer schema); `create_all()` on startup (creates missing tables but never alters existing ones,
so it silently does nothing on a schema change and would fail FR-018).

---

## R8. Refusing to run against a newer schema

**Decision**: Compare the version recorded in the database against the revisions the running code knows
about, and refuse to start when the database is ahead.

**Rationale**: FR-021. Alembic records the current revision in the database, and the running code carries
its revision history, so "the database is at a revision this code has never heard of" is directly
detectable. Without this check, older software would run against a newer schema and write data shaped for
a structure it does not understand.

---

## R9. Preventing two instances against one database

**Decision**: Acquire an exclusive advisory lock on a file inside the data directory at startup, released
on shutdown, and refuse to start when it is held.

**Rationale**: FR-005. SQLite protects individual writes but does not stop two application instances from
both running schedulers, both polling, and both sending the athlete notifications — the corruption here is
duplicated side effects rather than a damaged file. An explicit lock makes the second start fail loudly.

**Alternatives rejected**: relying on SQLite's own locking (protects the file, not the application
behaviour); a lock row in the database (a crashed instance leaves a stale row with no reliable way to tell
it from a live one).

---

## R10. Data carry-over scope is smaller than it appears — superseded

**Superseded during Phase 6 implementation**: the author decided to start the SQLite deployment with a
fresh account rather than migrate hosted data at all. `scripts/carry_over.py` was built and verified
against a structurally identical copy, then removed once that decision was made; spec.md's FR-023 through
FR-026 and the corresponding user story were removed with it. This section is kept as a record of the
reasoning that applied while carry-over was still in scope, not as current guidance.

**Original decision**: Carry across only locally originated data — plans, profile, adherence history,
conversation history. Re-fetch activity history from the training data source instead of transferring it.

**Original rationale**: The training data source is authoritative for activities, so transferring them
would be duplicating work that a re-fetch does more reliably. What remains is small, which keeps the
one-time carry-over simple and reviewable. Only the author has real data, so this is a one-time operation
rather than a supported capability.

---

## Resolved unknowns

| Unknown | Resolution |
|---|---|
| Async driver for the embedded engine | `aiosqlite`, to be added as a dependency; `asyncpg` removed at cutover |
| Portable UUID and JSON representation | `sqlalchemy.Uuid` and `sqlalchemy.JSON` (R3) |
| Preserving cascade semantics | Per-connection pragma, asserted by test (R1) |
| Preserving timestamp meaning | UTC-normalising type decorator (R2) |
| Preserving upsert | Dialect-selected `insert` construct (R4) |
| Concurrency strategy | WAL plus busy timeout (R5); pool class to be measured |
| Consistent live backup | `VACUUM INTO` (R6) |
| Automatic schema evolution | Alembic, already a dependency (R7) |
| Version guard | Alembic revision comparison at startup (R8) |
| Single-instance guard | Advisory file lock in the data directory (R9) |
