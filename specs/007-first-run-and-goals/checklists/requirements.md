# Specification Quality Checklist: First Run, Goals, and Coach Voice

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

**The specification was preceded by a discussion, and three decisions came from it.** Setup becomes a
confirmation rather than an interview; changing a goal is separated from starting over; and the coach's
voice becomes changeable at any time.

**Reading instead of asking is only safe because of FR-005 through FR-007.** Auto-detection has an obvious
failure mode: a threshold read from an account and never shown is a threshold the athlete cannot correct,
and a plan built on a stale one is wrong in a way they cannot detect and will nevertheless follow. The
confirmation screen is therefore not presentation polish — it is what makes the inference defensible.

**The correction path resolved a genuine conflict between two established rules.** The athlete must be able
to fix a wrong value, but spec 001 makes the training data source authoritative for the values it owns, and
spec 002 requires the coach's numbers to match what the athlete sees there. Keeping a local override would
satisfy the first rule and break the other two, producing exactly the divergence the metric-authority rule
exists to prevent. FR-006 therefore requires corrections to be written back to the source, and FR-007
requires the athlete to be directed there when write-back is impossible — never for the coach to quietly
hold a different value. This tension was not visible when spec 001 was written and only surfaced here.

**Separating the two reset paths removed a real hazard.** Today one action serves both "my goal changed"
and "start me over", and it discards context in both cases. The former is the common case in real use, and
it was destroying months of adherence history as a side effect of a goal change. FR-011 and FR-017 split
them; FR-018 and FR-019 make the destructive one deliberate rather than incidental.

**One decision has a structural consequence worth flagging to whoever plans this.** Making the voice
changeable at any time means it can no longer live solely in deployer configuration, where it currently
sits. It becomes athlete state with the configured value as its default (FR-024). This is a change to how
the existing persona mechanism is wired, not merely a new command over it.

**A documentation discrepancy was found and is recorded rather than acted on here.** The project's own
guidance describes a twelve-step onboarding with a provider-choice branch. The running code has six states.
The code is treated as authoritative and the stale documentation is noted in Assumptions; correcting it is
outside this feature.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
