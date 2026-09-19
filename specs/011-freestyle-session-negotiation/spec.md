# Feature Specification: Freestyle Session Negotiation

**Feature Branch**: `011-freestyle-session-negotiation`

**Created**: 2026-09-19

**Status**: Draft

**Input**: User description: Freestyle mode (spec 009) proposes a session on request, but the proposal
today ignores whatever the athlete actually said — it only looks at fitness state and rotates through the
session library by day. The athlete wants to actually ask for what they want ("I want an interval session",
"something short tonight", "not a pyramid") and have the coach listen, while every number in the answer
(target load, target duration) still comes from the deterministic engine, never from the language model.

## Scope

**In scope**: letting the athlete's natural-language request steer a freestyle-mode session suggestion —
an explicit desired workout type, a one-off style preference about which concrete session to offer, and a
maximum available duration for this specific ask. Honoring an explicit type request even when it conflicts
with what the fitness-driven default would have suggested, with a plain-language note instead of a refusal.

**Out of scope**: goal-mode plan generation; the existing training-load and recovery guardrail signals;
persisting a suggestion or a per-request preference beyond the single request/response exchange; changing
how the durable "disliked workout types" preference (existing coach memory) is stored or edited.

**Depends on**: freestyle coaching mode and its on-demand suggestion (spec 009); the session library and
its per-session fitting logic (spec 004); the existing durable athlete-preference memory that freestyle
mode already reads.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask for a specific type of session and get it (Priority: P1)

The athlete tells the coach what type of session they want — "I want an interval session" — even when
their recent training load or recovery state would normally lead the coach to suggest something else. The
coach gives them that type of session anyway, scaled to their real current fitness, and if it goes against
what it would have suggested unprompted, it says so plainly instead of refusing the request or silently
overriding it.

**Why this priority**: this is the core gap — today's suggestion ignores the conversation entirely, so an
explicit ask currently has no effect at all. Without this, "negotiation" doesn't exist.

**Independent Test**: ask the coach by name for a specific supported workout type and confirm the returned
suggestion is of that type, with an accompanying note whenever it conflicts with the fitness-driven default.

**Acceptance Scenarios**:

1. **Given** an athlete whose current fitness signal would normally lead to a recovery-type suggestion,
   **When** they explicitly ask for an interval session, **Then** the coach proposes an interval session
   scaled to their real fitness and recent load, and plainly states that it isn't what it would have
   suggested unprompted.
2. **Given** an athlete whose current fitness signal already favors the type they ask for, **When** they
   request that type, **Then** the coach proposes it with no conflict note.
3. **Given** an athlete who asks for a type the coach does not support at all, **When** they make that
   request, **Then** the coach says plainly that it doesn't offer that type rather than substituting
   something unrelated.

---

### User Story 2 - Steer which concrete session is offered (Priority: P2)

The athlete reacts to what's on offer in the moment — "not a pyramid, something steady" — without naming a
formal standing preference. The coach offers a different concrete session of the same type that better
matches, drawn only from sessions that already exist in the library, instead of always handing back the
same fixed rotation.

**Why this priority**: this makes the negotiation feel real, but it's a refinement of an already-working
type choice (P1), not the core "does it listen at all" gap.

**Independent Test**: ask for a session, react with a one-off style preference, and confirm the coach
offers a different concrete session of the same type that fits the stated preference — always one that
already exists in the library.

**Acceptance Scenarios**:

1. **Given** a suggested session, **When** the athlete says they don't want that specific structure and
   describes what they'd prefer instead, **Then** the coach offers a different session of the same type
   that matches the stated preference, provided the library has one available.
2. **Given** a stated style preference the library has no matching session for, **When** the coach
   responds, **Then** it offers the closest available session rather than fabricating a structure that
   does not exist in the library.

---

### User Story 3 - Respect a stated time limit (Priority: P3)

The athlete mentions they only have limited time tonight, and the returned suggestion fits within that
window instead of coming back with something too long.

**Why this priority**: lowest priority because a too-long suggestion can already be corrected today with a
follow-up message — it costs a round trip, not a missing capability — whereas User Stories 1 and 2 change
whether the negotiation works at all.

**Independent Test**: ask for a session while mentioning an available time window and confirm the returned
session's duration fits within it.

**Acceptance Scenarios**:

1. **Given** the athlete states a maximum available time, **When** a suggestion is generated, **Then** the
   returned session's duration does not exceed the stated ceiling.
2. **Given** a stated ceiling too short for any session of the chosen type to be fit, even after the
   system's existing tolerance relaxation, **When** the coach responds, **Then** it says plainly that
   nothing fits rather than returning an over-length session.

---

### Edge Cases

- Athlete asks for a workout type outside the four supported types → the coach says it doesn't have that
  type rather than silently mapping the request to something unrelated (see FR-008).
- Athlete's explicit one-time type request conflicts with a standing disliked-type preference already
  recorded in coach memory → the explicit request wins for this one suggestion only; the standing
  preference is untouched and applies again on the next request that doesn't name a type (see FR-009,
  Assumptions).
- Athlete's message carries no extractable preference at all ("just suggest me something") → the coach
  behaves exactly as it does today: fitness-driven type choice, existing rotation-based session pick (see
  FR-007).
- The chosen session ends up impossible to fit to the target load/duration even after the existing
  tolerance relaxation → the coach says so, the same way it already does today for this failure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST let an athlete's freestyle session request name a specific desired workout
  type in natural language, and the resulting suggestion MUST be of that type when it is supported.
- **FR-002**: When the requested type conflicts with the type the fitness-driven logic would otherwise have
  chosen, the system MUST still honor the requested type and MUST include a plain-language note explaining
  that it would not have been the default suggestion — never a refusal, mirroring how the system already
  treats other advisory-only signals rather than blocking the athlete outright.
- **FR-003**: Target duration and target training load for a requested session MUST be produced by the same
  deterministic engine logic used for an unprompted suggestion; the language model MUST NOT determine or
  override a load or duration figure (Constitution Principle I).
- **FR-004**: When the athlete expresses a one-off preference about which concrete session to offer, the
  system MUST choose only among sessions already defined in the library for the resolved workout type, and
  MUST NOT offer a session structure that does not exist in the library.
- **FR-005**: The system MUST reject any session choice that falls outside the concrete set of candidates
  available for the resolved workout type; an unrecognized or invalid choice MUST fall back to the current
  default selection rather than being trusted as-is.
- **FR-006**: When the athlete states a maximum available duration, the returned session's duration MUST
  NOT exceed it, unless no session of the resolved type can be fit within it even after the existing
  tolerance relaxation, in which case the system MUST say so instead of returning an over-length session.
- **FR-007**: When the athlete's request carries no preference the system can extract, the system MUST
  behave exactly as it does today — no change to the existing fitness-driven type choice or rotation-based
  session pick.
- **FR-008**: When the athlete asks for a workout type the system does not support at all, the system MUST
  say so plainly rather than silently substituting a different type.
- **FR-009**: An explicit, one-time workout-type request MUST take precedence over a previously recorded
  standing dislike of that type, for that single suggestion only; the standing preference MUST continue to
  apply on any later request that does not itself name that type.
- **FR-010**: A session suggestion produced through this negotiation MUST NOT be persisted beyond the
  single request/response exchange, consistent with how freestyle suggestions already behave today.
- **FR-011**: This feature MUST NOT change goal-mode plan generation or the existing training-load and
  recovery guardrail signals.

### Key Entities

- **Session Request Constraints**: the athlete's per-request preferences — desired workout type, maximum
  available duration, one-off style note — derived from a single natural-language ask. Exists only for the
  duration of one suggestion; never persisted; distinct from the durable "disliked workout types" note
  already stored in coach memory.
- **Session Candidate**: one of the concrete sessions already defined in the library for the resolved
  workout type, offered to the selection step by its identity and descriptive intent — never by a target
  load or duration figure, since those are only known once a candidate has been fit to the athlete.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete who explicitly names a supported workout type receives a suggestion of that type
  in 100% of requests, even when it conflicts with what the coach would have suggested unprompted.
- **SC-002**: Zero freestyle suggestions are ever of an unsupported or unrecognized workout type; every such
  request instead receives a plain statement that the type isn't available.
- **SC-003**: An athlete who states a maximum available time never receives a session exceeding that time,
  in 100% of requests where a fitting session exists.
- **SC-004**: 100% of session structures returned by this negotiation are sessions that already exist in the
  library — never a fabricated one.
- **SC-005**: A request with no extractable preference produces the same suggestion the current default
  logic would produce, in 100% of cases.

## Assumptions

- An explicit one-time workout-type request overrides a standing "disliked workout types" preference for
  that single suggestion only; the standing preference itself is not modified and resumes applying on
  subsequent requests that don't name a type.
- A one-off style preference ("not a pyramid") only steers which existing session is offered within the
  already-resolved workout type; it never changes the workout type itself, which is governed by the
  explicit-request/fitness-driven logic in FR-001/FR-002.
- Natural-language interpretation of the athlete's request may not catch every possible phrasing; a request
  where no preference is recognized falls back to today's default behavior (FR-007) rather than blocking
  the athlete or asking a clarifying question.
- No new persistence is introduced: session request constraints live only within the single request/response
  exchange, consistent with freestyle suggestions never being stored today.
