# Feature Specification: Publishing Planned Sessions to the Athlete's Calendar

**Feature Branch**: `005-planned-workout-push`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Send generated sessions to the athlete's training calendar, from where their own
head unit picks them up, so the athlete rides the session on their device instead of reading it on their
phone. Nothing is ever written without the athlete's explicit approval.

## Scope

**In scope**: publishing approved sessions to the athlete's training calendar, obtaining and recording
approval, keeping published sessions in step with the plan as it changes, withdrawing them, and coexisting
with edits the athlete makes on the other side.

**Out of scope**: talking to any head unit or device manufacturer directly. The athlete's training calendar
already forwards planned sessions to their device once they enable it there, and reproducing that is
neither necessary nor desirable. Also excluded: changing how sessions are generated or structured.

**Depends on**: the structured session work. A session described only as a type, a zone and a duration
cannot be executed by a device, so it cannot usefully be published. This feature is unbuildable until
sessions carry their steps.

**Inherited and not re-argued here**: spec 001 established that nothing may be written to the athlete's
calendar without explicit prior approval for the specific content being written.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The session is on the watch in the morning (Priority: P1)

The athlete asks the coach to send the week's training. The coach shows exactly what it intends to publish.
The athlete approves. The next morning they wake up, their head unit already has the session, and they ride
it without touching their phone.

**Why this priority**: This is the entire feature. It also changes what the product is: the coach stops
being something the athlete reads and becomes something that reaches them where they train.

**Independent Test**: Approve a week of sessions and confirm they appear in the athlete's training calendar
in a form their device can execute.

**Acceptance Scenarios**:

1. **Given** a plan containing structured sessions, **When** the athlete approves publication, **Then**
   those sessions appear in their training calendar on the correct dates.
2. **Given** a published session, **When** it is inspected in the calendar, **Then** it carries its steps,
   their durations, and their intensities, in a form the athlete's device can execute.
3. **Given** a published session, **When** the athlete's device synchronizes, **Then** the session is
   available on the device without further action from the athlete.
4. **Given** the athlete has not enabled forwarding to their device at their training calendar, **When**
   they publish, **Then** the coach tells them this step is theirs to perform and how, rather than leaving
   them wondering why nothing arrived.
5. **Given** a session that has no steps, **When** publication is attempted, **Then** it is reported as not
   publishable rather than published as an empty or fabricated session.

---

### User Story 2 - Nothing appears that the athlete did not agree to (Priority: P1)

The coach never writes to the athlete's calendar on its own initiative. Every publication is preceded by the
athlete seeing what will be written and agreeing to it. If they say no, nothing happens.

**Why this priority**: This is the product's first outbound mutation of data the athlete owns. Writing to
someone's training calendar unbidden is the fastest way to lose a privacy-conscious self-hosting audience,
and trust lost here contaminates every other feature.

**Independent Test**: Attempt every path that could produce a write and confirm each is gated behind a
recorded approval.

**Acceptance Scenarios**:

1. **Given** any path that would write to the calendar, **When** no approval has been given, **Then**
   nothing is written.
2. **Given** an approval request, **When** it is presented, **Then** it states which sessions, on which
   dates, will be written, before the athlete decides.
3. **Given** the athlete declines, **When** they decline, **Then** nothing is written and they are not asked
   again unprompted.
4. **Given** the athlete approves, **When** publication completes, **Then** they are told what was actually
   written, including anything that failed.
5. **Given** an approval was given for a specific set of sessions, **When** the plan later changes, **Then**
   that earlier approval does not authorize publishing the changed sessions.
6. **Given** publication occurs, **When** it is recorded, **Then** the approval that authorized it is
   recorded with it.

---

### User Story 3 - Publishing twice does not duplicate anything (Priority: P2)

The athlete publishes the week, then publishes again after a change. Their calendar contains one session per
planned session, not two. Sessions the coach published are recognisable as its own, and it never touches
anything else in the calendar.

**Why this priority**: Duplicated or orphaned entries make the athlete's calendar untrustworthy, and
cleaning them up by hand is exactly the tedium this feature exists to remove. It is P2 because a single
first publication delivers value before repetition is handled.

**Independent Test**: Publish the same period repeatedly and confirm the calendar converges rather than
accumulating.

**Acceptance Scenarios**:

1. **Given** sessions already published for a period, **When** the same period is published again, **Then**
   existing entries are updated rather than duplicated.
2. **Given** a calendar containing entries the coach did not create, **When** publication occurs, **Then**
   those entries are left untouched.
3. **Given** published entries, **When** they are examined, **Then** each is identifiable as originating
   from this coach and traceable to the planned session it came from.
4. **Given** a publication that fails partway, **When** it is retried, **Then** it completes without
   duplicating what already succeeded.

---

### User Story 4 - The calendar follows the plan (Priority: P2)

The athlete's week changes: a session is moved, shortened, or dropped after a conversation with the coach.
What is on their device changes to match, or is withdrawn. They are never left riding a session the coach no
longer intends.

**Why this priority**: A published session that has gone stale is worse than no published session, because
the athlete trusts it and rides it. It is P2 because it only arises once plans change after publication.

**Independent Test**: Publish a week, modify the plan, and confirm the calendar reflects the modification
after approval.

**Acceptance Scenarios**:

1. **Given** published sessions and a subsequent plan change, **When** the athlete approves republication,
   **Then** the calendar matches the revised plan.
2. **Given** a session removed from the plan, **When** the change is applied, **Then** its published entry
   is withdrawn rather than left behind.
3. **Given** published sessions that no longer match the plan, **When** the athlete has not yet approved
   republication, **Then** the coach tells them the calendar is out of date.
4. **Given** the athlete asks to withdraw everything, **When** they confirm, **Then** every entry the coach
   published is removed and nothing else is.
5. **Given** a session in the past, **When** the plan changes, **Then** already-elapsed sessions are not
   rewritten.

---

### User Story 5 - The athlete's own edits are respected (Priority: P3)

The athlete moves a session in their calendar themselves, or edits it. The coach notices rather than
silently overwriting their intent.

**Why this priority**: Overwriting a deliberate change made by the athlete is a small betrayal that teaches
them not to touch their own calendar. It is P3 because it requires the athlete to have adopted the feature
enough to start adjusting it.

**Independent Test**: Edit a published entry at the calendar, then republish, and confirm the edit is not
silently discarded.

**Acceptance Scenarios**:

1. **Given** a published entry the athlete has since edited, **When** republication would overwrite it,
   **Then** the athlete is told and decides.
2. **Given** a published entry the athlete has deleted, **When** republication occurs, **Then** it is not
   silently recreated without the athlete knowing.
3. **Given** the athlete completes a session, **When** publication runs for that period, **Then** the
   completed record is never altered.

### Edge Cases

- The calendar is unreachable partway through publishing several sessions.
- The athlete approves, then the connection fails entirely, leaving nothing published.
- Publication would exceed the calendar service's request quota.
- The athlete's plan is regenerated wholesale while a previous plan's sessions are still published.
- A session falls on a date already holding a completed activity.
- The athlete changes timezone, or a session sits near midnight, so the intended date is ambiguous.
- The athlete revokes their credential after sessions were published.
- The athlete stops using the coach entirely, leaving published sessions in their calendar indefinitely.
- A session's steps are valid to this system but rejected by the calendar as unrepresentable.
- Publication is requested for a period entirely in the past.
- Two publication attempts overlap.
- The athlete's device caches a session that has since been withdrawn.

## Requirements *(mandatory)*

### Functional Requirements

#### Approval

- **FR-001**: The system MUST NOT write to the athlete's calendar without explicit prior approval for the
  specific sessions being written.
- **FR-002**: An approval request MUST state which sessions, on which dates, will be written, before the
  athlete decides.
- **FR-003**: Declining MUST result in nothing being written, and MUST NOT cause the request to be repeated
  unprompted.
- **FR-004**: An approval MUST authorize only the content it was shown for; a subsequent change to that
  content MUST require fresh approval.
- **FR-005**: The system MUST record which approval authorized each publication.
- **FR-006**: After publication, the system MUST report what was actually written, including any failures.

#### Publication

- **FR-007**: The system MUST publish approved sessions to the athlete's training calendar on their planned
  dates.
- **FR-008**: A published session MUST carry its steps, their durations, and their intensities, in a form
  the athlete's device can execute.
- **FR-009**: The system MUST refuse to publish a session that lacks steps, and MUST report the refusal
  rather than publishing an empty or fabricated session.
- **FR-010**: The system MUST publish a bounded horizon rather than the entire plan, and that horizon MUST
  be stated to the athlete.
- **FR-011**: The system MUST NOT publish sessions whose dates have already passed.
- **FR-012**: The system MUST inform the athlete that forwarding from their calendar to their device is a
  setting only they can enable, and how to enable it.

#### Identity and idempotence

- **FR-013**: Every published entry MUST be identifiable as originating from this system and traceable to
  the planned session it represents.
- **FR-014**: Republishing a period MUST update existing entries rather than creating duplicates.
- **FR-015**: The system MUST NOT modify or remove calendar entries it did not create.
- **FR-016**: A publication interrupted partway MUST be resumable without duplicating what already
  succeeded.
- **FR-017**: Overlapping publication attempts MUST NOT produce duplicate entries.

#### Keeping in step with the plan

- **FR-018**: When the plan changes after publication, the system MUST be able to bring the calendar into
  agreement with it, subject to fresh approval.
- **FR-019**: A session removed from the plan MUST have its published entry withdrawn rather than left in
  place.
- **FR-020**: While published entries disagree with the plan, the system MUST tell the athlete the calendar
  is out of date.
- **FR-021**: The athlete MUST be able to withdraw everything the system published, and that action MUST
  affect nothing else.
- **FR-022**: Sessions whose dates have already passed MUST NOT be rewritten when the plan changes.

#### Respecting the athlete

- **FR-023**: When a published entry has been edited by the athlete, the system MUST NOT silently overwrite
  it; the athlete MUST be told and MUST decide.
- **FR-024**: When a published entry has been deleted by the athlete, the system MUST NOT silently recreate
  it.
- **FR-025**: The system MUST never alter a record of a completed activity.

#### Resilience

- **FR-026**: Publication failures MUST leave the calendar in a coherent state and MUST be reported to the
  athlete rather than failing silently.
- **FR-027**: Publication MUST stay within the calendar service's published request quotas.
- **FR-028**: A session the calendar rejects as unrepresentable MUST be reported specifically, and MUST NOT
  cause the rest of the publication to be abandoned.

### Key Entities

- **Publication approval**: The athlete's recorded consent to write a specific set of sessions on specific
  dates. Bounded to what was shown.
- **Published entry**: A session written to the athlete's calendar, identifiable as this system's and
  traceable to its planned session.
- **Publication horizon**: The bounded period ahead for which sessions are published.
- **Publication record**: What was published, when, and under which approval — the basis for updating,
  withdrawing, and detecting divergence.
- **Divergence**: The condition where published entries no longer match the plan, or have been changed by
  the athlete.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete approves a week and rides its sessions from their device without opening the coach
  again.
- **SC-002**: Zero calendar writes occur without a recorded approval, verified across every path capable of
  producing a write.
- **SC-003**: Publishing the same period repeatedly produces exactly one entry per planned session, verified
  across at least five consecutive publications.
- **SC-004**: Calendar entries not created by this system are never modified or removed, verified against a
  calendar seeded with foreign entries.
- **SC-005**: After a plan change and re-approval, 100% of future published entries match the revised plan,
  and entries for removed sessions are gone.
- **SC-006**: Withdrawal removes 100% of entries this system published and 0% of anything else.
- **SC-007**: An interrupted publication, once retried, results in the same calendar state as an
  uninterrupted one.
- **SC-008**: An athlete-edited entry is never silently overwritten, verified by editing entries and
  republishing.
- **SC-009**: The athlete is told, before their first publication, that enabling device forwarding is their
  own step, and can complete it from what they are told.
- **SC-010**: Publication of a full horizon stays within the calendar service's quota with margin to spare.

## Assumptions

- The athlete's training calendar is intervals.icu, which already forwards planned sessions to Garmin,
  Wahoo, and other head units once the athlete enables that in their own settings. This system writes to
  the calendar and stops there. Integrating with device manufacturers directly would duplicate a solved
  problem and add credentials, endpoints and failure modes for no gain.
- Enabling device forwarding is a setting inside the athlete's own calendar account. This system cannot do
  it on their behalf, which is why FR-012 requires telling them rather than assuming it is done — otherwise
  the first publication appears to do nothing and the feature seems broken.
- The calendar exposes a way to mark an entry as created by a particular application, which is what makes
  FR-013 through FR-015 achievable. Without a reliable ownership marker, safe republication and withdrawal
  would not be possible.
- A bounded horizon of the current and following week is the expected default, matching what comparable
  tools do. Publishing an entire multi-month plan would fill the athlete's calendar with sessions that will
  certainly change before they arrive.
- Approval is per publication rather than granted once and standing. A standing authorization would
  reintroduce exactly the unbidden-write problem this design exists to prevent, and plans change often
  enough that a stale authorization would frequently be wrong.
- Sessions must carry structured steps before they can be published. That is the preceding specification's
  responsibility; this one assumes it and refuses rather than improvises when a session lacks them.
- The athlete may legitimately edit or delete published entries. Treating their calendar as this system's
  exclusive property would be wrong: it is their calendar, and this system is a guest in it.
- Quotas are the same as those governing activity retrieval and are generous relative to a bounded horizon
  published a few times a week.
