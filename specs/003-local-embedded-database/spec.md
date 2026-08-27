# Feature Specification: Local Embedded Database

**Feature Branch**: `003-local-embedded-database`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Replace the hosted cloud database with a local embedded one living inside the
athlete's data directory, created and evolved automatically with no manual database administration, while
preserving every behaviour that currently depends on the hosted database's capabilities.

## Scope

**In scope**: the storage engine change, automatic schema creation and evolution, preservation of
behaviours that currently rely on database features not present by default in an embedded engine, safe
concurrent access, backup and restore, and moving the author's own existing data across.

**Out of scope**: schema redesign. This is a faithful port, not a remodelling. Consolidating tables,
renaming columns, or rethinking how activities and session logs relate are deliberately excluded, because
changing the storage engine and the schema shape in one step is how data gets lost. The only structural
changes permitted are the removal of storage made obsolete by spec 002.

**Depends on**: nothing. This is the first migration to perform.

**Implementation order — this specification comes before the provider migration.** Spec numbers are
identifiers, not a sequence, and this one is built first despite its higher number. The reason is that the
provider migration both adds storage (wellness records, notification markers, sync state) and removes it
(the previous provider's authorization records). Porting the storage engine first means porting a schema
that is standing still, and gives the provider migration an automatic schema-evolution mechanism to use.
Doing it the other way round means porting a moving target by hand, twice.

**Inherited and not re-argued here**: spec 001 established that all athlete data lives in one deletable
directory and that no separately administered database service may be required.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The database appears by itself (Priority: P1)

The operator starts the system for the first time. They do not create a database, do not create a user, do
not grant privileges, do not run a schema script, and do not connect with a database client. The system
creates its own storage inside the data directory and is ready to use. On every subsequent start it opens
what is already there without touching existing content.

**Why this priority**: Manual database provisioning is the single administrative step spec 001 promised to
eliminate. Until this works, the self-hosting claim is false.

**Independent Test**: On a clean machine, start the system with no database preparation of any kind and
confirm it reaches a working state.

**Acceptance Scenarios**:

1. **Given** a data directory with no existing storage, **When** the system starts, **Then** it creates its
   storage and completes startup without any manual database step.
2. **Given** storage that already exists and is current, **When** the system starts, **Then** it opens it
   and leaves all existing content intact.
3. **Given** the data directory is not writable, **When** the system starts, **Then** it fails immediately
   with a message identifying the directory and the permission problem.
4. **Given** a second instance is started against the same storage, **When** it attempts to open it,
   **Then** it either coordinates safely or refuses to start, and never corrupts the existing content.

---

### User Story 2 - Nothing silently changes meaning (Priority: P1)

The athlete's history, plans, profile, and coaching records behave exactly as before the change. Deleting a
parent record still removes its children. Structured records still round-trip unchanged. A value that was
unknown is still unknown rather than becoming zero or empty. Timestamps still refer to the same instants.

**Why this priority**: An embedded engine differs from a hosted one in ways that fail quietly rather than
loudly: referential integrity that is not enforced unless explicitly enabled, structured values that
degrade to text, timezone information that is dropped. Each produces corruption discovered weeks later.

**Independent Test**: Exercise every behaviour that depends on database semantics against the new storage
and compare results against the previous behaviour.

**Acceptance Scenarios**:

1. **Given** a record with dependent child records, **When** the parent is deleted, **Then** the children
   are removed too, exactly as before.
2. **Given** a record containing a structured document, **When** it is written and read back, **Then** it is
   identical, including nesting, types, and absent-versus-empty distinctions.
3. **Given** a structured document is modified in place, **When** the change is saved, **Then** it is
   persisted, and a modification is never silently discarded.
4. **Given** a record that already exists, **When** an insert-or-update is performed on it, **Then** the
   existing row is updated rather than duplicated, preserving current behaviour.
5. **Given** a stored timestamp, **When** it is read back, **Then** it denotes the same instant, and daily
   boundaries used for scheduling and matching are computed identically to before.
6. **Given** a value that was unknown, **When** it is stored and retrieved, **Then** it is still
   distinguishable from zero, false, and empty.

---

### User Story 3 - Concurrent work does not collide (Priority: P1)

Several things happen at once: a periodic refresh ingests an activity, a scheduler prepares a reminder, and
the athlete sends a message. All of them read and write. The athlete never sees an error caused by this,
and no write is lost.

**Why this priority**: The system runs several concurrent background activities against storage that, unlike
a hosted server, does not accept simultaneous writers by default. This is the most likely new class of
runtime failure introduced by the change, and it surfaces to the athlete as unexplained errors.

**Independent Test**: Drive concurrent ingestion, scheduled work, and conversation simultaneously and
confirm no failures surface and no writes are lost.

**Acceptance Scenarios**:

1. **Given** background work and athlete interaction occur simultaneously, **When** both write, **Then**
   both succeed and neither is lost.
2. **Given** a write is briefly blocked by another writer, **When** the block clears, **Then** the write
   completes without the athlete seeing an error.
3. **Given** sustained concurrent load, **When** the system runs, **Then** no operation fails with a
   storage-contention error surfaced to the athlete.
4. **Given** the system is stopped abruptly mid-write, **When** it restarts, **Then** the storage opens
   intact and no partially written record is visible.

---

### User Story 4 - Backup is copying one file (Priority: P2)

The operator wants a backup before a risky change. They run one documented command, or copy one file, and
have a complete restorable snapshot. Restoring it means putting the file back.

**Why this priority**: A self-hoster with no database administration skills must still be able to protect
their data. It is P2 because the system functions without it, but data loss is unrecoverable.

**Independent Test**: Back up a populated deployment, destroy it, restore from the backup, and confirm the
system resumes with all data intact.

**Acceptance Scenarios**:

1. **Given** a running system, **When** the operator performs the documented backup, **Then** a complete
   consistent snapshot is produced without stopping the system.
2. **Given** a backup and an empty deployment, **When** the operator performs the documented restore,
   **Then** the system starts with all data present and behaves as it did at backup time.
3. **Given** a backup taken while writes were in progress, **When** it is restored, **Then** it is
   internally consistent rather than containing a torn write.

---

### User Story 5 - The schema evolves without a database client (Priority: P2)

A new version of the software needs a schema change. The operator updates and restarts. The change is
applied automatically. They never open a database client, never run a script by hand, and never read a
migration note telling them to.

**Why this priority**: Today's schema changes require manually pasting SQL into a hosted console. That is
acceptable for one author and impossible for distributed self-hosters. It is P2 because the first release
needs only the initial schema.

**Independent Test**: Take a deployment on an older schema, upgrade the software, restart, and confirm the
schema updates itself with data intact.

**Acceptance Scenarios**:

1. **Given** storage on an older schema version, **When** a newer version starts, **Then** the schema is
   brought up to date automatically and existing data is preserved.
2. **Given** the schema is already current, **When** the system starts, **Then** no change is attempted and
   startup is not delayed meaningfully.
3. **Given** a schema change fails partway, **When** it fails, **Then** the storage is left in its previous
   working state and the failure is reported rather than leaving a half-changed schema.
4. **Given** storage from a newer version than the running software, **When** the older software starts,
   **Then** it refuses to run rather than corrupting data it does not understand.

---

### User Story 6 - The author's own history comes across (Priority: P3)

The author has real data in the hosted database: their plans, their profile, their coaching history, their
adherence record. After the change they still have it.

**Why this priority**: Only the author is affected, since nobody else runs this software yet. It is
nonetheless a one-way door: once the hosted database is gone, anything not carried across is lost forever.

**Independent Test**: Carry a copy of the real data across and confirm the coach behaves identically before
and after.

**Acceptance Scenarios**:

1. **Given** existing hosted data, **When** it is carried across, **Then** locally originated records —
   plans, profile, adherence history, conversation history — are present and unchanged in meaning.
2. **Given** activity history that also exists at the training data source, **When** the move is performed,
   **Then** it may be re-fetched from that source rather than transferred, since that source is
   authoritative for it.
3. **Given** the move has completed, **When** the coach is asked about past training, **Then** its answers
   match what it would have said before the move.
4. **Given** the move fails or is incomplete, **When** it is detected, **Then** the original hosted data is
   still intact and the move can be retried.

### Edge Cases

- The data directory is on a network or virtualized filesystem where file locking behaves differently.
  This must either work correctly or fail clearly, never corrupt silently.
- Storage grows large over years of history. Routine operations must remain responsive.
- The storage file is deleted while the system is running.
- Free disk space is exhausted during a write.
- The system is killed during automatic schema evolution.
- A structured document contains characters, very deep nesting, or numeric precision that the storage
  encoding handles differently from before.
- An identifier generated by the previous storage engine must remain valid and unique under the new one.
- A value stored as an unknown timestamp, an unknown number, and an empty structured document must remain
  distinguishable from one another after the change.
- Storage obsoleted by the removal of the previous training data provider still exists in carried-over data.

## Requirements *(mandatory)*

### Functional Requirements

#### Provisioning and lifecycle

- **FR-001**: The system MUST store all persistent data in an embedded store located inside the athlete's
  data directory, requiring no separately installed or administered database service.
- **FR-002**: The system MUST create its storage automatically on first start, with no manual schema step,
  script execution, or database client interaction.
- **FR-003**: The system MUST open existing storage on subsequent starts without altering its content.
- **FR-004**: The system MUST fail at startup with a specific, actionable message when the data directory is
  missing, unwritable, or otherwise unusable.
- **FR-005**: The system MUST prevent two instances from corrupting the same storage, either by
  coordinating access safely or by refusing the second start with a clear message.

#### Behavioural equivalence

- **FR-006**: Deleting a record MUST continue to remove its dependent records, and referential integrity
  MUST be actively enforced rather than assumed.
- **FR-007**: Structured documents MUST round-trip without loss of nesting, value types, or the distinction
  between an absent value and an empty one.
- **FR-008**: In-place modification of a structured document MUST be persisted reliably; a modification MUST
  never be silently discarded.
- **FR-009**: Insert-or-update behaviour MUST be preserved wherever it exists today, updating the existing
  record rather than creating a duplicate.
- **FR-010**: Stored timestamps MUST continue to denote the same instants, and the daily boundaries used for
  scheduling, matching, and weekly aggregation MUST be computed identically to before.
- **FR-011**: Unknown values MUST remain distinguishable from zero, from false, and from empty.
- **FR-012**: Existing record identifiers MUST remain valid and unique.

#### Concurrency and durability

- **FR-013**: Concurrent background work and athlete interaction MUST both be able to read and write without
  losing writes.
- **FR-014**: Transient write contention MUST be resolved internally and MUST NOT surface to the athlete as
  an error.
- **FR-015**: An abrupt stop MUST NOT leave the storage corrupt or expose a partially written record.

#### Backup and restore

- **FR-016**: The system MUST support producing a complete, internally consistent snapshot without being
  stopped.
- **FR-017**: Restoring a snapshot MUST return the system to the state captured, using a documented
  procedure that requires no database expertise.

#### Schema evolution

- **FR-018**: Schema changes MUST be applied automatically at startup, requiring no manual step by the
  operator.
- **FR-019**: Schema evolution MUST be idempotent: starting against an already-current schema MUST make no
  changes.
- **FR-020**: A failed schema change MUST leave the storage in its previous working state and report the
  failure, rather than leaving it partially changed.
- **FR-021**: The system MUST refuse to run against storage created by a newer version than itself.
- **FR-022**: The storage MUST record which schema version it is at, so that the running software can
  determine what to apply.

#### Data carry-over

- **FR-023**: Locally originated data — plans, profile, adherence history, and conversation history — MUST
  be carried across from the previous hosted database.
- **FR-024**: Activity history MAY be re-fetched from the training data source rather than transferred,
  since that source is authoritative for it.
- **FR-025**: A failed or incomplete carry-over MUST leave the original data intact and MUST be retryable.
- **FR-026**: Storage rendered obsolete by the removal of the previous training data provider MUST NOT be
  carried across.

### Key Entities

- **Data directory**: The single deletable location holding all athlete state, including the embedded store.
- **Embedded store**: The self-contained persistent storage, requiring no external service.
- **Schema version**: The recorded marker of which structural revision the storage is at.
- **Snapshot**: A complete, internally consistent copy of the store suitable for restoration.
- **Locally originated data**: Records this system produced and which exist nowhere else — plans, profile,
  adherence history, conversation history. Irreplaceable if lost.
- **Source-derived data**: Records obtained from the training data source, which can be re-fetched rather
  than migrated.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator reaches a working system with zero database administration actions: no service
  installed, no user created, no script run, no client opened.
- **SC-002**: Every behaviour that depends on database semantics produces identical results before and
  after the change, verified across dependent-record deletion, structured-document round-tripping,
  in-place modification, insert-or-update, timestamp interpretation, and unknown-value handling.
- **SC-003**: Sustained concurrent background work and athlete interaction produce zero storage-contention
  errors visible to the athlete, and zero lost writes.
- **SC-004**: An abrupt termination during writing leaves storage that opens intact on restart, verified
  across repeated trials.
- **SC-005**: An operator with no database expertise completes a backup and a full restore using only the
  documentation, and the restored system behaves as it did at backup time.
- **SC-006**: Upgrading a deployment on an older schema updates it automatically on restart with all data
  intact and no manual step.
- **SC-007**: After the author's data is carried across, the coach's answers about past training, current
  plan, and adherence history match what it said before the move.
- **SC-008**: Routine operations remain responsive against a store holding several years of history.

## Assumptions

- The embedded store is a single-file engine. Its default behaviour differs from the hosted database in
  three ways that are treated as first-class risks rather than details: referential integrity is not
  enforced unless explicitly enabled, concurrent writers are serialized rather than parallel, and there is
  no native timezone-aware timestamp type. FR-006, FR-013, and FR-010 exist because of these.
- Structured documents, unique identifiers, and insert-or-update are all expressible on the target engine,
  but not through the same vendor-specific constructs used today. Reaching identical behaviour is a
  portability exercise, not a redesign.
- This is a faithful port. Schema consolidation opportunities exist and are deliberately deferred, because
  combining a storage-engine change with a schema redesign makes any resulting data loss untraceable.
- Activity history is re-fetchable from the training data source, so the irreplaceable data is limited to
  what this system produced itself. This makes the carry-over small and low-risk.
- Only the author has real data. No third-party migration path is required, and the carry-over may
  therefore be a one-time operation rather than a supported ongoing capability.
- Storage obsoleted by spec 002 — in particular records supporting the removed provider's delegated
  authorization — is dropped rather than ported.
- The single-athlete design means data volume stays modest: years of history, not millions of rows.
  Performance requirements are correspondingly modest.
- The current implementation is the behavioural baseline. Where this spec and the running system disagree,
  the disagreement is a defect in this spec and should be raised rather than silently resolved.
