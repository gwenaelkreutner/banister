---
description: "Task list for feature 003 — local embedded database"
---

# Tasks: Local Embedded Database

**Input**: Design documents from `/specs/003-local-embedded-database/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/persistence.md](./contracts/persistence.md),
[quickstart.md](./quickstart.md)

**Tests**: Required. Constitution Principle V mandates test coverage for changes in this area, and three of
this feature's risks fail *silently* — they cannot be caught by observation, only by assertion.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task serves
- Exact file paths are included in every task

## Organization note — a deliberate deviation

The task template organizes phases by user story so each ships independently. **That does not apply here**,
and forcing it would produce a fiction.

This feature's user stories are not separable features — they are cross-cutting properties of one
migration. "The database appears by itself" (US1) cannot ship without the engine change, and that same
engine change is what delivers "nothing silently changes meaning" (US2) and "concurrent work does not
collide" (US3). There is exactly one deliverable increment: the storage engine changes, or it does not.

Phases below therefore follow the **plan's A–F sequence**, which reflects real dependency and real
revertibility. Story labels are retained on every task for traceability, and the mapping is stated at the
end. The independently verifiable checkpoints are the phase boundaries, not the stories.

---

## Phase 1: Setup

**Purpose**: Dependencies and the measurement baseline everything else is judged against

- [ ] T001 Add `aiosqlite>=0.20.0` to `dependencies` in `pyproject.toml` and run `uv lock` to regenerate `uv.lock`
- [ ] T002 [P] Create `tests/test_db/__init__.py` and `tests/test_db/conftest.py` with fixtures yielding both a PostgreSQL-backed and a SQLite-backed session, so every portability test can run against both
- [ ] T003 [P] Record the pre-change baseline in `specs/003-local-embedded-database/baseline.md`: exact passing test count (164), exact `ruff check app/ tests/` violation count (291), and the command used for each

**Why T003 matters**: Principle V requires lint to pass, which is currently impossible. The adopted gate is
"no new violations", and that is only enforceable against a recorded number.

**Checkpoint**: `uv sync` succeeds; baseline numbers are written down.

---

## Phase 2: Foundational — portable types (Plan Phase A)

**Purpose**: Make column types dialect-neutral **while still running on PostgreSQL**, so the existing suite
validates them against a known-good baseline.

**⚠️ CRITICAL**: This phase blocks everything after it. A failure here is unambiguously a type bug; the same
failure discovered after the engine change would be ambiguous between a type bug and an engine difference.

- [ ] T004 [US2] Create `UtcDateTime` type decorator in `app/db/types.py` that normalises any aware datetime to UTC on write and re-attaches UTC on read, and rejects naive datetimes on write rather than guessing their zone
- [ ] T005 [US2] Write `tests/test_db/test_types.py` asserting a datetime written with a non-UTC offset reads back as the same instant with `tzinfo` set — this test MUST fail against plain `DateTime(timezone=True)` on SQLite (see research R2)
- [ ] T006 [US2] Replace the timestamp columns in `app/db/models/base.py` (`TimestampMixin`) with `UtcDateTime`, and replace `server_default=func.now()` with a portable default producing the same instant on both backends
- [ ] T007 [P] [US2] Convert `app/db/models/user.py` to `sqlalchemy.Uuid(as_uuid=True)`, and make the boolean `server_default="true"` portable
- [ ] T008 [P] [US2] Convert `app/db/models/profile.py` to `Uuid` and `sqlalchemy.JSON`, preserving the `[]` vs `{}` defaults currently expressed as PostgreSQL-cast literals
- [ ] T009 [P] [US2] Convert `app/db/models/training_plan.py` to `Uuid` and `JSON` for `plan_technical` and `plan_narrative`
- [ ] T010 [P] [US2] Convert `app/db/models/session_log.py` to `Uuid` (×3) and `JSON` for `time_in_zones_s`, keeping `logged_date` a date rather than a timestamp
- [ ] T011 [P] [US2] Convert `app/db/models/chat_message.py` to `Uuid`
- [ ] T012 [P] [US2] Convert `app/db/models/activity.py` to `Uuid`, preserving the three-state nullable boolean `device_watts`
- [ ] T013 [P] [US2] Convert `app/db/models/weekly_adherence.py` to `Uuid`
- [ ] T014 [P] [US2] Convert `app/db/models/oauth_connection.py` to `Uuid` and `UtcDateTime` for `token_expires_at` — this table must survive the port intact and is dropped later by spec 002, not here
- [ ] T015 [US2] Write `tests/test_db/test_roundtrip.py` asserting a document containing nesting, `None`, `{}` and `[]` returns identical, and that `None`, `0`, `False` and `{}` remain four distinct stored states
- [ ] T016 [US2] Write a test in `tests/test_db/test_roundtrip.py` asserting a document mutated in place is persisted, covering the `flag_modified` hazard the project already documents
- [ ] T017 Run `uv run pytest tests/ -q` against PostgreSQL and confirm the count still matches the T003 baseline

**Checkpoint**: All models use dialect-neutral types. Suite green on PostgreSQL. Nothing about the engine
has changed yet, and this phase is revertible on its own.

---

## Phase 3: Foundational — portable upsert (Plan Phase B)

**Purpose**: Remove the last dialect-specific construct, still on PostgreSQL.

- [ ] T018 [P] [US2] Replace the PostgreSQL-specific `insert` import in `app/db/repositories/activity_repo.py` with a dialect-selected construct, preserving `on_conflict_do_nothing` behaviour in `bulk_insert`
- [ ] T019 [P] [US2] Replace the PostgreSQL-specific `insert` in `app/db/repositories/oauth_repo.py`, preserving `on_conflict_do_update` behaviour in `upsert_connection`
- [ ] T020 [P] [US2] Replace the PostgreSQL-specific `insert` in `app/db/repositories/weekly_adherence_repo.py`, preserving `on_conflict_do_update` on key `(user_id, week_start_date)`
- [ ] T021 [US2] Write `tests/test_db/test_upsert.py` asserting that calling each upsert twice with the same key yields one row carrying the second call's values, never two rows
- [ ] T022 Run the full suite against PostgreSQL and confirm it matches the T003 baseline

**Checkpoint**: No dialect-specific SQL remains in the repositories. Repository signatures are unchanged, as
required by [contracts/persistence.md](./contracts/persistence.md).

---

## Phase 4: Schema evolution (Plan Phase C) — US5

**Goal**: The schema updates itself, with no database client and no manual step.

**Independent Test**: Take a deployment on an older revision, start current software, confirm the schema
updates with data intact.

- [ ] T023 [US5] Initialise Alembic in `migrations/` with an async-aware `env.py` reading the database URL from `app/config.py` and importing metadata from `app/db/models`
- [ ] T024 [US5] Generate the baseline revision in `migrations/versions/` and verify a database created from it is structurally identical to one created from `migrations/init.sql`
- [ ] T025 [US5] Implement automatic migration on startup in `app/db/lifecycle.py`, invoked from the FastAPI lifespan in `app/main.py`
- [ ] T026 [US5] Implement the newer-schema guard in `app/db/lifecycle.py`: refuse to start when the recorded revision is unknown to the running code (FR-021)
- [ ] T027 [US5] Write `tests/test_db/test_migrations.py` covering: applying to an older schema preserves data; running against a current schema changes nothing (idempotence, FR-019); a failing migration leaves the previous working schema (FR-020); a newer-than-known revision refuses to start (FR-021)

**Checkpoint**: Schema evolution is automatic, idempotent, and guarded in both directions. `init.sql` still
exists and is removed at cutover, not here.

---

## Phase 5: The engine change (Plan Phase D) — US1, US2, US3, US4

**Goal**: SQLite becomes the storage engine and behaves like the database the application was written
against.

**Independent Test**: On a clean machine, `docker compose up` reaches a working system with no database
administration, and the full suite passes against the new engine.

### Connection layer

- [ ] T028 [US1] Change `app/config.py` so the database location is derived from a data-directory path rather than a hosted connection URL, and fail startup with a specific message when the directory is missing or unwritable (FR-004)
- [ ] T029 [US1] Rewrite engine creation in `app/db/client.py` for the async SQLite driver, replacing the PostgreSQL pool sizing that no longer applies
- [ ] T030 [US2] Add a connection-level event hook in `app/db/client.py` setting `PRAGMA foreign_keys=ON` on **every** connection — per-connection, not once at startup, because pooled connections would otherwise miss it (research R1)
- [ ] T031 [US3] Add `journal_mode=WAL`, `busy_timeout` and `synchronous=NORMAL` to the same connection hook in `app/db/client.py` (research R5)
- [ ] T032 [US3] Decide the connection pool class for the async driver, then **measure it** under the concurrency test rather than assuming — resolves open item 1 in plan.md

### The silent failures, asserted

- [ ] T033 [US2] Write `tests/test_db/test_cascade.py`: insert an athlete with dependent rows, delete the athlete, assert **zero** orphans remain
- [ ] T034 [US2] Verify T033 is meaningful by disabling the pragma and confirming the test **fails** — a cascade test that passes without the pragma is testing nothing
- [ ] T035 [US3] Write `tests/test_db/test_concurrency.py` driving simulated schedulers and interactive handlers writing simultaneously, asserting zero lost writes and zero contention errors reaching the caller (FR-013, FR-014)
- [ ] T036 [US3] Write `tests/test_db/test_durability.py` asserting the store opens intact after an abrupt termination mid-write, with no partially written record visible (FR-015)

### Lifecycle

- [ ] T037 [US1] Implement the single-instance advisory lock in `app/db/lifecycle.py` — a lock file in the data directory, so a crashed process releases it by dying rather than leaving a stale marker (research R9); resolves open item 2 in plan.md
- [ ] T038 [US1] Write `tests/test_db/test_lifecycle.py` asserting a second instance refuses to start and does not corrupt existing content (FR-005)

### Backup

- [ ] T039 [P] [US4] Implement `scripts/backup.py` producing a snapshot via `VACUUM INTO`, which is consistent without stopping the system (research R6, FR-016)
- [ ] T040 [P] [US4] Document the restore procedure in `scripts/backup.py` docstring and in the operator documentation — restoring is replacing the file, and must require no database expertise (FR-017)
- [ ] T041 [US4] Write a test asserting a snapshot taken **during active writing** restores to an internally consistent state rather than a torn one

### Convergence

- [ ] T042 Run the full suite against SQLite and confirm it matches the T003 baseline — the suite was written against the old engine, so its passing unchanged is the strongest single signal the port preserved behaviour

**Checkpoint**: The application runs on SQLite. All seven guarantees in
[contracts/persistence.md](./contracts/persistence.md) are asserted by tests, including the three that fail
silently by default.

---

## Phase 6: Data carry-over (Plan Phase E) — US6

**Goal**: The author's real history comes across. One-way door: anything not carried is lost.

**Independent Test**: Carry a copy across and confirm the coach's answers match what it said before.

- [ ] T043 [US6] Implement `scripts/carry_over.py` transferring only locally originated data — plans, profile, adherence history, conversation history — with a `--dry-run` mode reporting what would move (FR-023)
- [ ] T044 [US6] Make the script leave the source untouched and be safely retryable after a failure or interruption (FR-025)
- [ ] T045 [US6] Explicitly skip activity history, which is re-fetched from the training data source because that source is authoritative for it (FR-024)
- [ ] T046 [US6] Explicitly skip storage belonging to the removed provider integration where it is already obsolete (FR-026)
- [ ] T047 [US6] Run the carry-over against a **copy** of the real database and verify behaviourally: ask the coach about current plan, past training and adherence history before and after, and compare the answers — matching row counts prove much less than matching answers (SC-007)

**Checkpoint**: Real data is on the new engine and the coach behaves identically.

---

## Phase 7: Cutover (Plan Phase F)

**Purpose**: Remove what the migration replaced. Only after Phase 6 has been verified.

- [ ] T048 Remove the PostgreSQL service and its volume from `docker-compose.yml`, and mount the data directory instead
- [ ] T049 [P] Remove `asyncpg` from `pyproject.toml` and regenerate `uv.lock`
- [ ] T050 [P] Delete `migrations/init.sql`, now superseded by the Alembic baseline
- [ ] T051 Remove the hosted-database connection settings from `app/config.py` and from `.env.example`
- [ ] T052 Verify no PostgreSQL-specific import remains: `grep -rn "dialects.postgresql\|asyncpg" app/` must return nothing

**Checkpoint**: PostgreSQL is gone from the project.

---

## Phase 8: Polish & validation

- [ ] T053 Run every scenario in [quickstart.md](./quickstart.md) end to end on a clean machine
- [ ] T054 Run the contract check from quickstart scenario 9: `git diff --stat` must show no router, service, engine or LLM module — anything else means the change escaped its scope
- [ ] T055 Run `uv run ruff check app/ tests/` and confirm no violations beyond the T003 baseline of 291
- [ ] T056 [P] Repeat the concurrency and durability tests (T035, T036) at least twenty times — both are timing-dependent, and a single pass proves considerably less than twenty
- [ ] T057 [P] Update `CLAUDE.md`: the storage section, the `migrations/` description, and the environment variables, per the refactor banner's instruction to update it *during* each migration rather than after
- [ ] T058 [P] Update `README.md` install instructions, removing database provisioning steps that no longer exist
- [ ] T059 Verify SC-008: routine operations stay responsive against a store holding several years of history

---

## Dependencies & Execution Order

### Phase dependencies

```
Phase 1 (Setup)
   └─> Phase 2 (Portable types, on PostgreSQL)   ⟵ blocks everything
          └─> Phase 3 (Portable upsert, on PostgreSQL)
                 └─> Phase 4 (Alembic)
                        └─> Phase 5 (Engine change → SQLite)
                               └─> Phase 6 (Carry-over)
                                      └─> Phase 7 (Cutover)
                                             └─> Phase 8 (Validation)
```

Strictly sequential by phase. That is not conservatism — it is what keeps every failure attributable to
the change that caused it, which is the core argument of the plan.

### Within phases

- Phase 2: T007–T014 are all `[P]` — nine different model files, no shared state. T006 first (the mixin
  they inherit), T015–T017 last.
- Phase 3: T018–T020 are `[P]` — three different repository files.
- Phase 5: T039–T041 (backup) are independent of T028–T038 and can proceed in parallel.
- Phase 8: T056–T058 are `[P]`.

### The one ordering trap

**T034 must follow T033 and must be run deliberately.** It verifies the cascade test by disabling the
pragma and confirming failure. Skipping it leaves a test that would pass whether or not the feature works —
the exact silent failure the test exists to catch.

---

## Story → phase mapping

| Story | Priority | Delivered by | Verified by |
|---|---|---|---|
| US1 — Database appears by itself | P1 | Phases 4, 5 | T027, T028, T038; quickstart 1 |
| US2 — Nothing silently changes meaning | P1 | Phases 2, 3, 5 | T005, T015, T016, T021, T033, T034; quickstart 2–3 |
| US3 — Concurrent work does not collide | P1 | Phase 5 | T035, T036, T056; quickstart 4–5 |
| US4 — Backup is copying one file | P2 | Phase 5 | T039–T041; quickstart 6 |
| US5 — Schema evolves without a client | P2 | Phase 4 | T027; quickstart 7 |
| US6 — The author's history comes across | P3 | Phase 6 | T047; quickstart 8 |

---

## Implementation Strategy

### There is no MVP subset

Unlike a feature built from independent slices, this migration has one deliverable increment. Phases 1–5
must all complete before the system runs on the new engine; stopping earlier leaves the application on
PostgreSQL with portability changes applied but nothing gained.

What the phases do provide is **safe stopping points**. Phases 2 and 3 are behaviour-preserving on
PostgreSQL and can be merged and left in place indefinitely without committing to the migration. That is
where the risk reduction lives.

### Recommended sequence

1. **Phases 1–3** — merge these first. They are reversible, behaviour-preserving, validated by the existing
   suite, and they carry no engine risk. Landing them separately shrinks the risky change.
2. **Phase 4** — Alembic. Also low-risk, still on PostgreSQL, and it delivers US5 on its own.
3. **Phase 5** — the actual migration. Everything genuinely risky is concentrated here, which is the point
   of the ordering.
4. **Phase 6** — carry-over, against a copy first, always.
5. **Phases 7–8** — remove and verify.

### Before starting Phase 6

Take a full backup of the real database and confirm the restore works. Phase 6 is the only one-way door in
this feature.

---

## Notes

- `[P]` means different files with no shared dependency
- Commit at every phase checkpoint; every phase boundary is a working state
- Three tests assert failures that are otherwise silent (T033/T034, T005, T015). They are the most valuable
  tasks in this list and the easiest to write in a way that quietly passes without proving anything
- `oauth_connections` survives this feature intact. Dropping it belongs to spec 002 — putting it here would
  place a spec-002 change inside a spec-003 commit (open item 3 in plan.md, now resolved)
