# Specification Quality Checklist: intervals.icu as Sole Training Data Source

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

**Decisions taken during drafting rather than deferred:**

1. *Polling interval fixed at five minutes* — the author asked for this spec to settle the interval. Five
   minutes bounds notification delay to five minutes while consuming roughly 6% of the published daily
   quota, leaving margin for history import and retries. Recorded in FR-007 with the reasoning in
   Assumptions, and made configurable with an enforced floor so an operator cannot accidentally exhaust
   the quota.

2. *Structural defects expressed as outcomes, not designs* — the audit found four concrete defects on the
   surviving code path (direct data access inside an event handler, a fabricated stand-in object built to
   satisfy an interface, dead imports, and a value whose availability depends on a guard condition being
   duplicated identically). These became FR-038 through FR-041, phrased as verifiable outcomes so the spec
   does not prescribe an implementation while still ensuring the defects are not carried forward.

3. *Threshold-change immutability made explicit* — FR-022 and a matching edge case were added because
   per-activity values are captured under the thresholds in force at the time. Without stating this,
   changing a threshold could retroactively alter the meaning of stored history, which would violate the
   metric-consistency promise this migration exists to deliver.

4. *Unknown distinguished from zero* — FR-020 and FR-024 state that a missing metric or wellness value is
   recorded as unknown. This is the most likely silent corruption in a migration of this shape: a source
   that has not yet finished computing a load value returns nothing, and a naive ingest stores zero, which
   then propagates into chronic load as if the athlete had rested.

**Content quality note**: the specification names intervals.icu only in Scope, Key Entities, and
Assumptions. Every requirement is phrased against "the training data source" so that the requirements
remain about capability rather than about one vendor's interface.

**Deferred by design** — stated here, specified elsewhere: acting on wellness data (guardrails spec),
the local datastore migration, calendar writes, and the onboarding rework.

**Iteration 2 — cross-specification analysis:**

5. *Two preserved capabilities were orphaned* — spec 001 requires the weekly review and the daily reminder
   to survive the migration, but no specification claimed them. The weekly review is not unaffected: it
   consumes load values that now come from the source rather than from local computation. FR-034a and
   FR-034b make the obligation explicit rather than leaving it to be discovered after a regression.

6. *Test coverage stated* — the project constitution requires engine changes to ship with tests, and no
   specification said so. FR-034c adds it, including the less obvious half: removing superseded local
   calculations must remove the tests that covered only them without weakening coverage of what remains.

7. *Build order corrected* — this specification now declares a dependency on the database migration. It
   both adds storage and removes storage, so building it before the port would mean porting a schema that
   is changing underneath the port.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
