# Quickstart: Validating the Local Embedded Database

**Feature**: 003-local-embedded-database | **Date**: 2026-08-27

How to prove this feature works. Every scenario below maps to a success criterion in
[spec.md](./spec.md) and is runnable without reading the implementation.

---

## Prerequisites

```bash
uv sync                       # installs aiosqlite alongside existing dependencies
uv run ruff check app/ tests/ # baseline: no NEW violations vs. the 291 pre-existing
uv run pytest tests/ -q       # baseline: 164 passing
```

The 164-test baseline is the regression contract. It was written against the previous engine, so its
passing unchanged is the strongest single signal that the port preserved behaviour.

---

## Scenario 1 — The database appears by itself (SC-001)

Proves FR-001 through FR-003: no database administration of any kind.

```bash
rm -rf ./data && docker compose up -d
docker compose logs app | grep -i "schema\|migration"
ls -la ./data/
```

**Expect**: the store exists under the data directory, the log shows migrations applied, and at no point
was a database service installed, a user created, a script run, or a client opened. Restart and confirm
existing content is untouched and no migration re-runs (FR-019).

**Also verify the failure paths**:

```bash
chmod -w ./data && docker compose restart app   # FR-004: names the directory and the permission problem
docker compose up -d --scale app=2              # FR-005: second instance refuses, does not corrupt
```

---

## Scenario 2 — Cascade deletes actually cascade (SC-002, FR-006)

The highest-value test in this feature, because the failure is silent.

```bash
uv run pytest tests/test_db/test_cascade.py -v
```

**Expect**: a test that inserts an athlete with dependent rows, deletes the athlete, and asserts **zero**
orphans remain. Confirm the test is meaningful by removing the connection pragma and re-running — it must
fail. A cascade test that passes with the pragma disabled is testing nothing.

Independent check against the live store:

```bash
sqlite3 ./data/banister.db "PRAGMA foreign_keys;"   # note: per-connection, so this reflects the CLI's
                                                     # own connection, not the app's — the test is the
                                                     # authoritative check
```

---

## Scenario 3 — Stored values keep their meaning (SC-002, FR-007/010/011)

```bash
uv run pytest tests/test_db/test_roundtrip.py -v
```

**Expect** four assertions, each of which fails silently in a naive port:

| Assertion | Fails as |
|---|---|
| A document with nesting, `None`, `{}` and `[]` returns identical | Validation error far from the cause |
| A timestamp written with an offset returns with that offset | Reminders and matching drift by hours |
| `None`, `0`, `False`, `{}` remain four distinct states | A missing metric becomes a rest day in chronic load |
| A document mutated in place is persisted | A plan modification silently discarded |

---

## Scenario 4 — Concurrent work does not collide (SC-003, FR-013/014)

```bash
uv run pytest tests/test_db/test_concurrency.py -v
```

**Expect**: simulated schedulers and interactive handlers writing simultaneously, with zero lost writes and
zero contention errors raised to the caller. This is the scenario to run repeatedly rather than once — it
is timing-dependent, so a single pass proves less than twenty.

---

## Scenario 5 — Abrupt termination leaves the store intact (SC-004, FR-015)

```bash
docker compose kill -s SIGKILL app   # during active writing
docker compose up -d app
uv run pytest tests/ -q
```

**Expect**: the store opens, no partially written record is visible, and the suite still passes. Repeat
several times; a single trial does not establish durability.

---

## Scenario 6 — Backup and restore without expertise (SC-005, FR-016/017)

```bash
uv run python scripts/backup.py --out ./backup.db   # while the system is running
rm -rf ./data && docker compose up -d               # destroy
cp ./backup.db ./data/banister.db && docker compose restart app
```

**Expect**: the system resumes with all data present and behaves as it did at backup time. The snapshot is
taken live, so also take one *during* active writing and confirm it restores to an internally consistent
state rather than a torn one.

---

## Scenario 7 — The schema evolves by itself (SC-006, FR-018/020/021)

```bash
# Downgrade one revision to simulate an older deployment, then start current software
uv run alembic downgrade -1 && docker compose restart app
```

**Expect**: the schema is brought up to date automatically, data intact, no manual step.

**Then test the guard** (FR-021): point software carrying an older revision history at a store stamped with
a newer revision. It must refuse to start rather than writing data shaped for a structure it does not
understand.

**And the failure path** (FR-020): a migration that fails partway must leave the previous working schema,
not a half-changed one.

---

## Scenario 8 — The author's history comes across (SC-007, FR-023/025)

Run against a **copy**, never the live source.

```bash
uv run python scripts/carry_over.py --from "$POSTGRES_URL" --to ./data/banister.db --dry-run
uv run python scripts/carry_over.py --from "$POSTGRES_URL" --to ./data/banister.db
```

**Expect**: plans, profile, adherence history and conversation history present and unchanged in meaning.
Activity history is **not** transferred — it is re-fetched from the training data source, which is
authoritative for it (FR-024).

**The real acceptance test is behavioural, not structural**: ask the coach the same questions before and
after — current plan, past training, adherence history — and compare the answers. Matching row counts prove
much less than matching answers.

Confirm also that the source database is untouched and the operation is retryable (FR-025).

---

## Scenario 9 — The contract held (contracts/persistence.md)

```bash
git diff --stat main...HEAD -- app/ | grep -v "^ app/db/"
```

**Expect**: only `app/config.py` and `app/main.py`. **Any router, service, engine, or LLM module appearing
here means the change escaped its scope** — the storage engine leaked past the repository boundary, and the
port is not the isolated change it claims to be.

---

## Definition of done

- [ ] All nine scenarios pass
- [ ] The 164-test baseline passes unchanged against the new engine
- [ ] No new lint violations beyond the 291 pre-existing
- [ ] Scenario 2 fails when the pragma is disabled (proving the test is real)
- [ ] Scenarios 4 and 5 pass on repeated runs, not a single lucky one
- [ ] Scenario 9 shows no diff outside the persistence boundary
