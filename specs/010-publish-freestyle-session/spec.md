# Feature Specification: Publish a Freestyle Session

**Feature Branch**: `010-publish-freestyle-session`

**Created**: 2026-09-18

**Status**: Draft

**Input**: User description: Freestyle mode (spec 009) lets the athlete ask for a session and discuss it
in chat, but nothing happens if they like it — it is never written anywhere, so it cannot be followed on a
Garmin or a home trainer. The athlete wants to negotiate a session (ask for something different if the
first proposal doesn't suit them) and, once happy with one, get it pushed to their intervals.icu calendar
as a single structured workout — reusing the same content rendering and calendar-write mechanism spec 005
already built for plan sessions, for one ad-hoc session outside any plan. The athlete is open on the exact
confirmation mechanism (a button, or a chat-driven confirmation) and explicitly asked for the trade-off
between the two to be weighed before committing to one.

## Scope

**In scope**: turning an already-negotiated freestyle session suggestion (spec 009) into a real entry on
the athlete's intervals.icu calendar once they confirm they want it, and letting them withdraw it later if
they change their mind.

**Out of scope**: how a session is selected or negotiated in the first place (spec 009, unchanged by this
feature); anything about plan-mode `/publish`/`/unpublish` (spec 005, unchanged — this is a second, smaller
write path for the freestyle case, not a replacement).

**Depends on**: spec 009 (the session suggestion this feature publishes) and spec 005 (the content
rendering and calendar-write/consent patterns this feature reuses for a single ad-hoc session instead of a
plan's whole horizon).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish a session you're happy with (Priority: P1)

The athlete asks the coach for a session, likes what's proposed, and confirms it. The exact session they
were just shown — same type, duration, structure — appears on their intervals.icu calendar, so they can
follow it on their bike computer or home trainer.

**Why this priority**: This is the whole point. Without it, a freestyle suggestion is advice with nothing
the athlete can actually load onto their device — the gap the athlete explicitly flagged.

**Independent Test**: In freestyle mode, ask for a session, confirm it, and check the athlete's intervals.icu
calendar for a matching structured workout on the expected date.

**Acceptance Scenarios**:

1. **Given** the coach has just proposed a freestyle session, **When** the athlete confirms they want it,
   **Then** a single structured workout appears on their calendar matching exactly what was proposed (type,
   duration, structure).
2. **Given** the athlete has not been shown any session yet (or the topic has moved on), **When** they try
   to confirm a publication, **Then** the coach tells them plainly there is nothing pending to publish,
   rather than publishing something stale or guessed.
3. **Given** the calendar write fails (e.g. the athlete's connection to intervals.icu is temporarily down),
   **When** that happens, **Then** the athlete is told the publication did not go through, never told it
   succeeded.

---

### User Story 2 - Ask for something different before committing (Priority: P2)

The athlete doesn't like the first session proposed, asks for another one, and only the session they
eventually confirm gets published — never an earlier one they'd already moved past.

**Why this priority**: Without this, "negotiation" is just words — confirming would risk publishing
whichever suggestion happened to be computed last on the server, not the one the athlete actually agreed
to, undermining trust in the whole feature.

**Independent Test**: Ask for a session, ask for a different one, confirm, and check that only the second
session (not the first) was written to the calendar.

**Acceptance Scenarios**:

1. **Given** the coach proposed session A and the athlete then asked for something else and got session B,
   **When** the athlete confirms, **Then** session B — not session A — is what gets published.
2. **Given** the athlete asked for a new suggestion after already publishing an earlier one, **When** that
   happens, **Then** the earlier published entry is left untouched unless the athlete separately asks to
   remove it (US3) — a new request never silently replaces what's already on the calendar.

---

### User Story 3 - Change your mind after publishing (Priority: P3)

The athlete published a freestyle session but no longer intends to do it (plans changed, weather, etc.) and
wants it off their calendar, the same way they can already withdraw plan-mode published sessions.

**Why this priority**: Consistency with the existing withdrawal guarantee (spec 005) — an athlete who
already trusts `/unpublish` for plan sessions should get the same guarantee here; lower priority than P1/P2
because publishing something one doesn't want is recoverable manually today (delete it on intervals.icu
directly), just less convenient.

**Independent Test**: Publish a freestyle session, then withdraw it, and confirm it disappears from the
calendar while everything else the athlete or other tools put there stays untouched.

**Acceptance Scenarios**:

1. **Given** a freestyle session the coach published is still in the future, **When** the athlete asks to
   remove it, **Then** it is removed from the calendar and from what the coach considers "published."
2. **Given** the athlete's calendar also has entries from a plan, from intervals.icu itself, or from
   another tool, **When** a freestyle session is withdrawn, **Then** none of those other entries are
   touched (mirrors spec 005's "100% of ours, 0% of anything else" guarantee).

---

### Edge Cases

- What happens if the athlete's fitness state changes between being shown a session and confirming it
  (e.g., they log a ride in between)? The publication must reflect exactly what they were shown and agreed
  to, not a freshly recomputed session — confirming isn't a request for a new suggestion.
- What happens if the athlete switches from freestyle mode to goal mode (spec 009) while a freestyle session
  is still published for a future date? Resolved the same way spec 009 already resolves the equivalent case
  for plan sessions: withdrawn automatically as part of the switch (see Assumptions).
- What happens if the athlete asks to publish the same confirmed session twice (e.g., a repeated tap)? The
  second attempt must not create a duplicate calendar entry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: While discussing a session suggestion in freestyle mode, the athlete MUST be able to ask for
  a different one instead of accepting the current proposal.
- **FR-002**: Every session suggestion the coach proposes MUST be presented with an attached confirmation
  button; publication happens only when that button is tapped — ordinary conversational agreement alone
  (e.g. an "ok" said in passing) MUST NOT trigger a publication (resolved by Clarification Question 1,
  Option A: unambiguous by construction, no new LLM tool involved in the confirmation step itself).
- **FR-003**: Upon confirmation, the system MUST write the confirmed session to the athlete's intervals.icu
  calendar as a single structured workout, using the same content-rendering mechanism the existing
  plan-session publish path (spec 005) already uses, so it is followed identically on a connected device.
- **FR-004**: The system MUST NOT publish any session the athlete has not explicitly confirmed, even if
  several candidate sessions were discussed in the same conversation (US2).
- **FR-005**: Tapping a confirmation button for a suggestion that has since been superseded by a newer one
  (US2) or has expired (FR-005a) MUST tell the athlete plainly that it's no longer current rather than
  publishing the stale session underneath it (US1 Acceptance Scenario 2).
- **FR-005a**: A confirmation button MUST stop being usable once its suggestion is no longer the current
  one — either because the athlete asked for something different (US2) or because enough time or
  conversation has passed that it's no longer clearly "the one just discussed."
- **FR-006**: Each published freestyle session MUST be identifiable by the system as its own, so that a
  withdrawal action affects only entries this system wrote for freestyle sessions — never a plan-published
  entry, an entry the athlete created directly, or one from another tool (mirrors spec 005 FR-015/FR-025).
- **FR-007**: The athlete MUST be able to withdraw a previously published freestyle session that has not
  yet occurred (US3).
- **FR-008**: If the calendar write fails, the athlete MUST be told the publication did not succeed —
  never told or left to assume it did (US1 Acceptance Scenario 3).
- **FR-009**: The session content that gets published (type, duration, structure, target zone/load) MUST
  be exactly what deterministic engine output (spec 009) produced and the athlete was shown — never
  recomputed, altered, or reworded by the LLM narration step before being written (Constitution Principle
  I).
- **FR-010**: Confirming the same already-published session a second time MUST NOT create a duplicate
  calendar entry (Edge Cases).
- **FR-011**: Switching from freestyle mode to goal mode (spec 009) MUST withdraw any freestyle sessions
  still published for a future date, the same way that switch already withdraws plan-published entries
  (spec 009 FR-013) — kept symmetric rather than leaving a second, inconsistent kind of stale calendar entry
  behind.

### Key Entities

- **Pending Freestyle Suggestion**: the specific session currently under discussion — what the athlete
  would be confirming if they said yes right now. Held only for the duration of that negotiation; superseded
  the moment the athlete asks for something different (US2), and gone once they confirm it, decline it, or
  move on to another topic (FR-005).
- **Published Freestyle Entry**: a calendar entry this system wrote for a confirmed freestyle session —
  distinct from a plan-published entry (spec 005) so the two withdrawal paths never cross each other's
  entries, and individually identifiable so one can be withdrawn (US3) without touching any other.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete who is happy with a proposed freestyle session gets it onto their calendar with a
  single confirmation action, with no additional back-and-forth required.
- **SC-002**: 100% of published freestyle sessions match exactly what the athlete was shown immediately
  before they confirmed — verified by comparing the calendar entry's content to the last suggestion shown
  in that conversation.
- **SC-003**: An athlete can always find and remove a freestyle session they published but no longer intend
  to do, and doing so never removes or alters anything else on their calendar.
- **SC-004**: Zero freestyle sessions are ever published without an explicit, single, unambiguous
  confirmation tied to that specific session (no accidental publications from ordinary conversation).

## Assumptions

- This feature reuses spec 005's calendar-write mechanism and content rendering (DSL) as-is for a single
  session; it does not change how plan-mode `/publish`/`/unpublish` behave.
- A freestyle session is published for today by default unless the athlete specifies another date while
  discussing it; this feature does not add a separate date-picking flow beyond what the conversation already
  establishes.
- Switching from freestyle to goal mode withdraws any still-future published freestyle entries, mirroring
  spec 009's existing goal→freestyle withdrawal (FR-011) — chosen for consistency rather than introducing a
  second, different rule for what happens to freestyle-mode calendar writes on a mode switch.
- Only one freestyle suggestion is "pending confirmation" at a time per athlete, consistent with the
  single-user, single-conversation-thread nature of the product (Constitution Principle II).
- Confirmation is a keyboard button attached to the suggestion message (Option A, confirmed) — no new LLM
  tool is introduced for the confirmation step itself; publication happens unambiguously by construction
  when the button is tapped, and the earlier suggestion tool from spec 009 is unaffected. Negotiation (US2,
  "give me something different") still happens in chat exactly as spec 009 already built it — only the
  final "publish this" step becomes a button.
