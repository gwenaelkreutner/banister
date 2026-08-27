# Specification Quality Checklist: Training Guardrails

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

**Silence was specified as carefully as warning.** The obvious way to write this feature is to enumerate
thresholds and fire on them. That produces a coach that cries wolf, and an athlete who learns to dismiss it —
at which point the one warning that mattered is dismissed too. Three requirements exist specifically to
prevent that failure, and they were the least obvious part of this specification:

- FR-013 forbids evaluating a threshold before enough history exists. A twenty-percent deviation from a
  baseline computed on four days of data is noise presented as insight.
- FR-011 forbids a single anomalous reading from firing a signal. Wearables produce bad readings, and one
  of them should not cancel an athlete's session.
- FR-005 forbids manufacturing a warning when values are normal, which is the failure mode of a system
  rewarded for appearing vigilant.

**The verification requirement is the one that makes the rest worth having.** A guardrail that states a
fabricated number is worse than no guardrail, because the athlete cannot tell which of the coach's figures
to trust and must therefore distrust all of them. FR-017 through FR-021 require every stated value to be
checked against what was actually retrieved, and require failures to be *counted* — without FR-021, SC-001
would be an aspiration rather than a measurement. This is the concrete form of the validation checklist
that spec 001 required in the abstract.

**Advisory rather than authoritative, decided deliberately.** A guardrail that cancels sessions on its own
would be more decisive and would be wrong sometimes — a bad night's sleep has causes this system cannot
see. Making it authoritative would require it to be right. FR-023 through FR-027 keep the decision with the
athlete while keeping the obligation to speak with the coach. FR-026 additionally forbids escalating into
nagging when the athlete repeatedly overrides, which is how well-intentioned safety features become
adversarial.

**Constitutional constraint restated as a requirement.** FR-022 requires all guardrail evaluation to be
deterministic, with the language model narrating findings it was handed rather than producing them. This is
Principle I of the project constitution applied to a feature where the temptation to let the model reason
about the numbers is strongest. It is also what makes SC-010 achievable: findings must be reproducible to
be testable at all.

**Two hazards drawn from the edge cases rather than from the requirements list**, and worth flagging to
whoever plans this work: a long layoff makes the chronic baseline very low, so a normal first session back
produces an alarming ratio; and a deliberate overload block produces a high ratio by design. Both are
correct arithmetic producing a wrong conclusion. They are recorded as edge cases rather than requirements
because the right handling is a design decision, not a stated outcome.

**Boundary held**: this feature raises signals and adjusts training. It does not diagnose. FR-028 through
FR-030 are requirements rather than documentation notes because the boundary matters legally as well as
ethically.

**Status**: All checklist items pass. Ready for `/speckit-plan`.

Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
