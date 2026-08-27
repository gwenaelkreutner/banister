# Specification Quality Checklist: Self-Hosted Open Source Pivot

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

**Iteration 1 findings and corrections applied:**

1. *Implementation detail leakage* — the initial draft named specific technologies (SQLite, Docker,
   intervals.icu, Telegram, aiogram) throughout the requirements. Corrected: requirements now describe
   capabilities ("embedded local datastore", "container orchestration command", "training data source",
   "messaging platform"). Concrete product names are retained only in the Assumptions and Key Entities
   sections, where naming the actual external dependency is necessary and appropriate.

2. *Untestable success criteria* — "easy to deploy" replaced with SC-001 (under 15 minutes, documentation
   only) and SC-002 (enumerated zero-infrastructure conditions).

3. *Scope boundary* — an explicit Scope section was added because this spec is deliberately a framing spec
   whose implementation is delegated to four downstream specs. Without it, readers would expect migration
   detail that intentionally is not here.

4. *Mid-drafting scope correction* — manual session logging was removed from scope at the author's
   direction. This propagated to FR-010 (remove rather than retain superseded local calculations), to
   User Story 1 acceptance scenario 4 (startup fails without a data source), and to three Assumptions
   entries. The earlier "fallback" framing was withdrawn: with a mandatory data source there is no
   unconnected mode for a fallback to serve.

**Iteration 2 — omission reported by the author:**

5. *Missing capability: proactive post-activity notification* — the initial draft covered no unsolicited
   outbound communication at all. The existing product notifies the athlete after every ride and treats
   that as its main engagement loop; as written, the spec would have permitted losing that behaviour
   during the migration without violating any requirement. Added User Story 3 (P2), FR-N01 through FR-N08,
   six edge cases, and SC-007/SC-008. Subsequent user stories and success criteria were renumbered.

6. *Contradictory assumption withdrawn* — the draft asserted that "near-real-time reaction to a completed
   activity is not required," which directly contradicts the newly added requirement. Replaced with an
   assumption that states the actual trade-off: notification is refresh-driven rather than push-driven
   because accepting an inbound push would reintroduce the public endpoint FR-002 exists to eliminate, and
   the athlete accepts a bounded delay in exchange. The specific delay is deliberately left to the provider
   spec as a product decision rather than guessed here.

**Deferred by design** — the following are stated as requirements here and specified in detail elsewhere:
FR-019 through FR-023 (guardrails), and the implementation of FR-003, FR-007, and FR-009.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
