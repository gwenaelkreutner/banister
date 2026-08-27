# Feature Specification: First Run, Goals, and Coach Voice

**Feature Branch**: `007-first-run-and-goals`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Turn setup from an interrogation into a confirmation. Read everything the
training data source already knows about the athlete, show it, let them fix what is wrong, and ask only the
two things no system can know: what they are training for, and when. Separate changing a goal from starting
over. Let the athlete change the coach's voice whenever they like.

## Scope

**In scope**: the first-run experience, correcting what was read, capturing the goal and the constraints
only the athlete knows, changing a goal later without discarding history, deliberately starting over, and
selecting the coach's voice.

**Out of scope**: how a plan is generated once the inputs exist, and how sessions are structured. This
feature gathers and confirms inputs; generation is unchanged.

**Depends on**: the training data source connection, which supplies almost everything this feature would
otherwise ask for.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Setup is a confirmation, not an interview (Priority: P1)

The athlete starts the coach for the first time. Instead of being asked a series of questions about
themselves, they are shown what the coach already knows: their threshold, their heart rate figures, their
weight, their current fitness, and how much they have been training. They check it, and it is right. Then
they are asked the only two things the coach cannot know — what they are training for and when — and their
plan is generated.

**Why this priority**: This is the feature. It also removes the most common reason a new user abandons
setup, which is being asked for information they know the system could have looked up.

**Independent Test**: Connect an account with a complete profile and confirm setup completes with two
questions and one confirmation.

**Acceptance Scenarios**:

1. **Given** a connected account with a complete profile, **When** setup begins, **Then** the athlete is
   shown the values that were read before being asked anything about themselves.
2. **Given** the displayed values, **When** the athlete confirms them, **Then** they are asked only what
   they are training for and when.
3. **Given** the goal and date are supplied, **When** setup completes, **Then** a plan is generated without
   further questions.
4. **Given** setup completes, **When** the athlete reviews what the plan was built from, **Then** every
   input is visible to them.
5. **Given** the athlete has constraints that affect what they can safely train, **When** setup runs,
   **Then** they are asked, since no data source can know this.

---

### User Story 2 - Nothing is assumed silently (Priority: P1)

A value read from the athlete's account is wrong or out of date — a threshold from last winter, a weight
that changed. The athlete sees it, says so, and it is corrected at the source, so their coach and their
training log agree from then on.

**Why this priority**: Silent inference is worse than asking. A plan built on a stale threshold is wrong in
a way the athlete cannot detect, and they will follow it. This requirement is what makes reading-instead-of-
asking safe rather than merely convenient.

**Independent Test**: Present a stale value, correct it, and confirm both the coach and the source reflect
the correction.

**Acceptance Scenarios**:

1. **Given** a value that was read rather than supplied, **When** it is shown, **Then** the athlete is told
   where it came from and, where known, how old it is.
2. **Given** a value the athlete says is wrong, **When** they correct it, **Then** they are shown what will
   change at the source and from what to what, and the write happens only after they approve it.
3. **Given** an approved correction, **When** it is written, **Then** it reaches the training data source
   rather than being held only locally, and the approval is recorded with it.
4. **Given** a correction cannot be written to the source, **When** the athlete corrects it, **Then** they
   are told to change it at the source and why, rather than the coach quietly keeping a different value.
5. **Given** a value is missing at the source, **When** setup runs, **Then** the athlete is asked for it
   rather than having a default substituted silently.
6. **Given** the athlete has no measured threshold, **When** setup runs, **Then** the coach proceeds using
   what the athlete does have, and says which basis it is using.
7. **Given** the athlete has little or no training history, **When** setup runs, **Then** documented
   conservative assumptions are used and the athlete is told they were.

---

### User Story 3 - Changing the goal does not erase the athlete (Priority: P2)

Three months in, the athlete picks a different event. They tell the coach. A new plan is built from where
they actually are now — their current fitness, their history, their adherence record intact. They do not
start over as though they had never trained.

**Why this priority**: This is the common case in real use, and today it is served by the same action as
starting over, which discards context the athlete has spent months accumulating. It is P2 because it only
arises after the athlete has been using the coach for a while.

**Independent Test**: Change the goal on a populated deployment and confirm the new plan reflects current
fitness while history and adherence survive.

**Acceptance Scenarios**:

1. **Given** an athlete with training history, **When** they change their goal, **Then** the new plan is
   built from their current fitness rather than from a standing start.
2. **Given** a goal change, **When** the new plan is created, **Then** completed sessions, adherence
   history, and conversation history are preserved.
3. **Given** a goal change, **When** it happens, **Then** the athlete is not asked again for information
   that has not changed.
4. **Given** sessions were published to the athlete's calendar under the previous plan, **When** the goal
   changes, **Then** the divergence is surfaced rather than left silently stale.
5. **Given** a goal change, **When** the athlete reviews the result, **Then** they can see what changed and
   what carried over.

---

### User Story 4 - Starting over is possible and deliberate (Priority: P2)

The athlete wants a genuinely clean slate. They can have one, but not by accident: they are told what will
be discarded and must confirm.

**Why this priority**: Conflating this with a goal change is what makes the current single action dangerous.
Separating them means neither has to be cautious about the other's consequences.

**Independent Test**: Start over and confirm the discard is explicit, warned, and complete.

**Acceptance Scenarios**:

1. **Given** an athlete with history, **When** they ask to start over, **Then** they are told specifically
   what will be discarded before anything happens.
2. **Given** the warning, **When** they decline, **Then** nothing is discarded.
3. **Given** they confirm, **When** the reset completes, **Then** the discard is complete rather than
   partial.
4. **Given** a reset, **When** it completes, **Then** data held at the training data source is untouched,
   since the coach does not own it.

---

### User Story 5 - The athlete picks who is coaching them (Priority: P2)

The athlete finds the coach's tone too clinical, or wants it in another language. They change it, and the
next thing the coach says sounds different. They can change it again whenever they like.

**Why this priority**: A single hardcoded voice is wrong for an open source project serving people in
different languages who want different things from a coach. It is P2 because the coach functions with the
default voice.

**Independent Test**: Change the voice and confirm subsequent output follows it, repeatedly and in both
directions.

**Acceptance Scenarios**:

1. **Given** the available voices, **When** the athlete views them, **Then** each is described well enough
   to choose between them.
2. **Given** the athlete selects a voice, **When** the coach next speaks, **Then** it uses that voice.
3. **Given** a voice has been selected, **When** the system restarts, **Then** the selection persists.
4. **Given** a voice is changed, **When** past records are examined, **Then** they are unchanged.
5. **Given** no voice has been selected, **When** the coach speaks, **Then** it uses the deployer's
   configured default.
6. **Given** a selected voice is no longer available, **When** the coach speaks, **Then** it falls back to
   the default and says so rather than failing.

---

### User Story 6 - The athlete knows what they are using (Priority: P3)

Before the coach gives its first advice, the athlete is told plainly that it is software, not a physician or
a certified coach, and that its sessions are suggestions.

**Why this priority**: Required by the guardrails specification, and first run is where it belongs. It is
P3 in build order only.

**Independent Test**: Confirm the statement appears before the first coaching interaction.

**Acceptance Scenarios**:

1. **Given** a new athlete, **When** they begin, **Then** they are told this before receiving any coaching
   advice.
2. **Given** they have been told, **When** they continue using the coach, **Then** it is not repeated at
   every interaction.

### Edge Cases

- The connected account has almost nothing filled in, so there is little to confirm.
- A threshold exists at the source but is very old, and the athlete has clearly progressed since.
- The athlete's stated goal date is in the past, or so near that no meaningful plan can be built, or so far
  away that a full plan would be speculative.
- The athlete changes their goal to a date earlier than their current plan's end.
- The athlete corrects a value that the source will not accept.
- The athlete abandons setup halfway.
- Setup is started again while a plan already exists.
- The athlete's declared available hours conflict sharply with what their history shows they actually do.
- The athlete reports a constraint that makes the goal they just stated unrealistic.
- The voice is changed mid-conversation.
- The configured default voice does not exist.

## Requirements *(mandatory)*

### Functional Requirements

#### Reading before asking

- **FR-001**: Setup MUST read everything the training data source can supply about the athlete before
  asking them anything about themselves.
- **FR-002**: Setup MUST present what was read for confirmation before proceeding.
- **FR-003**: Setup MUST ask only for what cannot be read: what the athlete is training for, when, and any
  constraints affecting what they can safely train.
- **FR-004**: Every input a plan is built from MUST be visible to the athlete.

#### Correcting what was read

- **FR-005**: A value that was read MUST be shown with its origin and, where known, its age.
- **FR-006**: A correction to a value owned by the training data source MUST be written back to that source.
- **FR-006a**: Writing a correction back to the source is an outbound mutation of the athlete's account and
  MUST therefore be subject to the same consent rule as any other: the athlete MUST be shown what will be
  changed at the source, from what to what, and MUST approve it before it is written. The athlete stating
  a correct value is not by itself approval to modify their account.
- **FR-006b**: A write-back MUST record the approval that authorized it.
- **FR-006c**: A refused or failed write-back MUST leave the source unchanged and MUST fall through to
  FR-007 rather than the coach retaining a divergent local value.
- **FR-007**: When a correction cannot be written to the source, the athlete MUST be directed to change it
  there, and the coach MUST NOT retain a value that disagrees with the source.
- **FR-008**: A value missing at the source MUST be requested rather than defaulted silently.
- **FR-009**: When the athlete has no measured threshold, the system MUST proceed on what they do have and
  state which basis it is using.
- **FR-010**: When history is insufficient, the system MUST use documented conservative assumptions and
  disclose that it has done so.

#### Goals

- **FR-011**: The athlete MUST be able to change their goal without discarding completed sessions,
  adherence history, or conversation history.
- **FR-012**: A plan built after a goal change MUST start from the athlete's current fitness rather than
  from a standing start.
- **FR-013**: A goal change MUST NOT re-ask for information that has not changed.
- **FR-014**: When a goal change invalidates sessions already published to the athlete's calendar, the
  divergence MUST be surfaced.
- **FR-015**: The system MUST reject or challenge a goal date that is in the past or too near to plan for,
  rather than producing a meaningless plan.
- **FR-016**: After a goal change, the athlete MUST be able to see what changed and what carried over.

#### Starting over

- **FR-017**: Starting over MUST be a distinct action from changing a goal.
- **FR-018**: Starting over MUST state specifically what will be discarded before anything is discarded.
- **FR-019**: Starting over MUST require explicit confirmation, and declining MUST discard nothing.
- **FR-020**: Starting over MUST NOT alter data held at the training data source.

#### Coach voice

- **FR-021**: The athlete MUST be able to change the coach's voice at any time, not only during setup.
- **FR-022**: Available voices MUST be presented with enough description to choose between them.
- **FR-023**: A selected voice MUST take effect on the coach's next output and MUST persist across
  restarts.
- **FR-024**: Voice selection MUST be stored as athlete state, with the deployer's configured value serving
  as the default when none has been selected.
- **FR-025**: Changing the voice MUST NOT alter any existing record.
- **FR-026**: When a selected or configured voice is unavailable, the system MUST fall back to a working
  default and say so rather than failing.

#### Disclaimer

- **FR-027**: The athlete MUST be told, before receiving any coaching advice, that the system is neither a
  physician nor a certified coach and that its sessions are suggestions.
- **FR-028**: That statement MUST NOT be repeated at every interaction once acknowledged.

### Key Entities

- **Read profile**: The set of athlete attributes obtained from the training data source, each carrying its
  origin and, where known, its age.
- **Athlete-only inputs**: What no data source can supply — the goal, its date, and constraints affecting
  safe training.
- **Goal**: What the athlete is training for and when, the input that shapes periodization.
- **Coach voice**: The selectable identity, tone, and language the coach speaks with, stored as athlete
  state and defaulting to the deployer's configured value.
- **Reset**: The deliberate, confirmed discarding of locally held athlete data, affecting nothing at the
  training data source.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete with a complete connected account completes setup by confirming one screen and
  answering two questions.
- **SC-002**: Zero athlete attributes obtainable from the training data source are asked for.
- **SC-003**: Every value presented for confirmation shows where it came from, and its age where known.
- **SC-004**: Zero values silently diverge between the coach and the training data source after a
  correction.
- **SC-005**: Changing a goal preserves 100% of completed sessions, adherence history, and conversation
  history.
- **SC-006**: Starting over discards nothing without an explicit confirmation, and discards everything
  stated once confirmed.
- **SC-007**: Zero records held at the training data source are altered by starting over.
- **SC-008**: A voice change is reflected in the coach's next output and survives a restart.
- **SC-009**: The disclaimer precedes the first coaching advice in 100% of new deployments.
- **SC-010**: An athlete completes setup in under 3 minutes with a connected account.

## Assumptions

- The current setup asks six questions covering sport, goal, target date, weekly volume, power meter, and
  age. The project's own documentation describes a longer flow that no longer exists; the code is the
  authority and the documentation is stale.
- Of those six, the training data source can supply the sport, the threshold and whether a power meter is
  in use, and the physiological figures, and can indicate actual training volume. It cannot supply the goal
  or its date. Weekly volume is treated as an athlete input rather than a read value, because what the
  athlete has been doing is not the same as what they intend to do.
- Corrections are written back to the source rather than held locally. Holding a local override would
  reintroduce exactly the divergence between the coach's numbers and the athlete's training log that the
  metric-authority rule exists to prevent. Where write-back is impossible, directing the athlete to the
  source is the only option that preserves a single source of truth.
- Because the athlete may change the coach's voice at any time, voice cannot live solely in deployer
  configuration. It becomes athlete state, with the configured value as its default. This is a change from
  the present arrangement, where it is configuration only.
- **Known starting point, verified by inspection**: the mechanism for loading a coach voice from a
  definition file already exists and works, several voice definitions are already written, and the
  deployer-level configuration value already exists. What does not exist is any call to it: the prompt
  layer still holds its text inline, across roughly six places, and under two different coach identities.
  This work is therefore not a build from nothing — it is finishing an existing, unfinished piece and
  then making the result selectable. Whoever plans it should read the prompt layer first, because the
  remaining work is concentrated there rather than in the loading mechanism.
- A second, separate mechanism for narrative modes also lives in the prompt layer. It overlaps in shape
  with coach voices but is a different axis and must not be merged into them: a voice is who the coach is,
  a narrative mode is how one activity is recounted. Both survive.
- Voice and narrative mode remain orthogonal: the voice is who the coach is, while a narrative mode governs
  how a single activity is recounted. Selecting a voice does not select a narrative mode.
- Health constraints continue to be asked, since no data source knows them and they materially affect what
  can be safely prescribed.
- Plan generation is unchanged. This feature alters what is gathered and how, not what is produced from it.
- The current implementation is the behavioural baseline for plan generation triggered at the end of setup.
  Where this spec and the running system disagree, the disagreement is a defect in this spec and should be
  raised rather than silently resolved.
