# Specification Quality Checklist: Structured Workouts and Session Library

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

**The blast radius drove the central design decision.** An inspection found that planned-session attributes
are read in roughly twenty modules across plan generation, plan modification, matching, adherence scoring,
reminders, conversational context, and post-activity analysis. A specification that simply replaced the flat
representation with a structured one would have required rewriting all of them simultaneously, in a change
whose stated purpose is unrelated to any of them.

The specification therefore requires the change to be **additive**: steps are added, and the existing
summary attributes are retained but redefined as values *derived* from the steps (FR-008, FR-009). This has
two effects worth stating plainly. Existing readers keep working untouched, so the regression surface stays
small. And because the summary is derived rather than stored alongside, it cannot drift out of agreement
with the steps — a failure mode that would otherwise appear months later as a session whose parts do not
add up to its whole. The cost is deliberate redundancy, accepted knowingly.

**Backward compatibility was treated as a data problem, not a code problem.** Plans are already persisted as
structured documents, so plans in the previous shape exist right now. FR-013 and User Story 5 exist because
a schema change that cannot read them would destroy the athlete's in-progress training block. FR-014
requires that a session genuinely lacking steps report their absence rather than have plausible ones
invented, which is the tempting shortcut and the one that would silently misprescribe training.

**Two hazards found by inspection and specified rather than left implicit:**

1. *Absolute intensities rot.* A library storing target watts becomes wrong the moment an athlete's
   threshold improves, and does so silently. FR-022 and FR-025 require relative expression, while also
   requiring that completed sessions keep the values recorded at the time — the same immutability rule
   spec 002 applies to per-activity metrics.

2. *The description attribute has a language baked into its name.* Descriptions are currently assembled from
   French text templates inside the generator. For a project defaulting to English with a configurable coach
   voice, this cannot survive. FR-028 through FR-030 move description generation onto the structure and the
   configured voice. This is adjacent to the persona work, which is already built but not yet wired in.

**Ordering justified**: this specification is a hard prerequisite of the calendar-push feature, not a
companion to it. A session described as a type, a zone and a duration cannot be transmitted to a device
that must execute it. The dependency is stated in Scope so the two are not attempted in the wrong order.

**Attribution obligation recorded**: the intended source for library content is MIT licensed and must be
credited, with its licence preserved. Its structure is adopted; its training content is to be reviewed
rather than copied unexamined.

**Iteration 2 — cross-specification analysis:**

*A requirement in spec 001 was actively endangered by this specification, and nothing here said so.* Spec
001 requires the offline plan-quality evaluation capability to be preserved. This specification replaces
how sessions are produced — the exact thing that capability evaluates. As written, every one of its
requirements could have been satisfied while leaving that capability broken, because none of them mentioned
it. This is the same failure mode found earlier in spec 001 itself, where a rewritten component had no
requirement naming what depended on it.

FR-014a through FR-014c and SC-005a/SC-005b close it: evaluation must still run, must be able to compare
plans generated before and after so quality can be shown not to have regressed, and the change must ship
with tests per the constitution. SC-005b additionally requires the reminder and weekly review to be
verified against sessions carrying steps rather than assumed unaffected.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
