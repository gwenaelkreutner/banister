# Specification Quality Checklist: Local Embedded Database

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-27
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Validation Notes

**Grounded in an inspection of the current data layer.** The requirements below are not generic
database-portability boilerplate; each corresponds to something the running system actually relies on:

1. *Referential integrity is the highest silent-corruption risk* — every model declares cascading deletes.
   The target engine does not enforce foreign keys unless explicitly told to, and when it does not, it
   fails silently: deletes appear to succeed and orphaned rows accumulate undetected. FR-006 therefore
   requires enforcement to be active, not merely declared.

2. *Concurrency is a genuinely new failure class* — the system runs several concurrent background
   activities alongside conversation handling. The current hosted database accepts parallel writers; the
   target engine serializes them. This surfaces to the athlete as unexplained errors rather than as
   corruption, which is why it earned a P1 user story of its own rather than a footnote.

3. *Three vendor-specific constructs are in active use* — structured-document columns, generated unique
   identifiers, and insert-or-update. All three are expressible on the target engine, but not through the
   constructs used today. FR-007, FR-009 and FR-012 state the required behaviour rather than the mechanism.

4. *In-place modification of structured documents is already a known trap in this codebase* — the project
   documents that such changes must be explicitly flagged or they are silently discarded. FR-008 carries
   that hazard forward so the port cannot quietly reintroduce it.

5. *Timezone handling has no native equivalent* — timestamps are currently stored timezone-aware. The
   target engine has no such type. Because daily boundaries drive reminders, weekly aggregation, and
   activity-to-session matching, FR-010 requires those boundaries to be computed identically rather than
   merely requiring timestamps to be "preserved".

**Scope decision recorded deliberately**: this is a faithful port, not a schema redesign. Consolidation
opportunities were noticed during inspection and excluded. Combining a storage-engine change with a schema
remodelling makes any resulting data loss impossible to attribute, and both changes are individually
reversible only while they remain separate.

**Iteration during implementation — data carry-over removed entirely**: the original draft required
carrying locally originated data (plans, profile, adherence history, conversation history) across from the
hosted database, on the reasoning that activity history is re-fetchable from the training data source and
so the irreplaceable surface was small. That reasoning held, but the author decided during Phase 6's
implementation to start the SQLite deployment with a fresh account instead of migrating real data at all —
superseding the premise, not just the execution. The former User Story 6, FR-023 through FR-026, SC-007,
and the "Locally originated data" / "Source-derived data" key entities were removed rather than left
satisfied-but-unused. A related decision surfaced at the same time: authorization tokens for the previous
sport-data provider are not carried either, since the athlete reconnects fresh — independent of, and not
changing, when the table backing those tokens is dropped from the schema, which remains spec 002's
responsibility because that provider is still the one in active use until spec 002 replaces it.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
