# Feature Specification: Structured Workouts and Session Library

**Feature Branch**: `004-structured-workouts`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Make a planned session expressible as an ordered sequence of executable steps —
warm-up, efforts, recoveries, repeats, cool-down — instead of a single zone and duration, and build a
reusable library of session templates the plan generator draws from. This is what a session must become
before it can be sent to the athlete's head unit.

## Scope

**In scope**: enriching a planned session so it describes what the athlete actually does minute by minute,
a reusable library of session templates, having the plan generator draw from that library, and doing all of
this without disturbing the many parts of the system that already read planned sessions.

**Out of scope**: sending sessions anywhere. Writing to the athlete's calendar or head unit is specified
separately and depends on this work. Also excluded: changing periodization logic, changing how weekly load
targets are set, and changing how completed activities are matched to planned sessions.

**Why this comes before the push feature**: a session described only as "intervals, Z4, 60 minutes, 65 TSS"
cannot be transmitted to a device, because a device needs the actual steps. This specification exists to
close that gap; the push feature depends on it entirely.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A session says what to actually do (Priority: P1)

The athlete opens today's session. Instead of "intervals in zone 4 for 60 minutes", they see the session as
they will ride it: a warm-up, a set of efforts with recoveries between them, and a cool-down, each with its
own duration and intensity. They know before starting exactly how the hour is shaped.

**Why this priority**: This is the capability the whole specification exists to add, and everything else
here depends on it. It also delivers immediate value on its own, before anything is ever transmitted to a
device.

**Independent Test**: Generate a plan and confirm every session describes an ordered sequence of steps whose
durations and intensities are individually stated.

**Acceptance Scenarios**:

1. **Given** a generated plan, **When** the athlete views a session, **Then** it presents an ordered
   sequence of steps rather than a single undifferentiated block.
2. **Given** a session containing repeated efforts, **When** it is described, **Then** the repetition is
   expressed as a repeated group rather than as the same step listed many times.
3. **Given** any step, **When** it is described, **Then** it states its duration and its intended intensity.
4. **Given** a session of a type that has no internal structure, such as a steady endurance ride, **When**
   it is described, **Then** it is still expressed as steps, with a single effort step, rather than being a
   special case.
5. **Given** a session's steps, **When** their durations are summed, **Then** the total equals the session's
   stated duration, and the two can never disagree.

---

### User Story 2 - Everything that already reads sessions keeps working (Priority: P1)

The plan display, the daily reminder, the matching of completed activities to planned sessions, the
adherence score, the coach's conversational context and its plan-modification tool all continue to behave
exactly as before. None of them needed changing to accommodate the richer sessions.

**Why this priority**: Planned sessions are read throughout the system. A change that forces every reader to
be rewritten at once is a change that will introduce regressions across features this work is not otherwise
touching.

**Independent Test**: Exercise every existing capability that reads planned sessions and confirm identical
behaviour before and after.

**Acceptance Scenarios**:

1. **Given** the enriched sessions, **When** any existing feature reads a session's type, intensity,
   duration, or load target, **Then** those remain available and carry the same meaning as before.
2. **Given** a session's steps, **When** its summary values are read, **Then** they are derived from the
   steps rather than stored independently, so the two cannot drift apart.
3. **Given** the change is complete, **When** activity-to-session matching runs, **Then** it produces the
   same results it produced before.
4. **Given** the change is complete, **When** adherence is scored, **Then** it produces the same results it
   produced before.
5. **Given** the coach proposes a plan modification, **When** the modification is applied, **Then** the
   resulting session remains internally consistent, with its steps and its summary agreeing.

---

### User Story 3 - Sessions come from a library, not from string assembly (Priority: P2)

The plan generator no longer invents each session from scratch. It selects an appropriate template from a
library of known-good sessions and fits it to the athlete's targets. A contributor can read the library,
understand what each session is for, and add a new one without touching the generator.

**Why this priority**: A library makes session quality reviewable and extensible by people who understand
training but not the codebase, which matters for an open source project. It is P2 because the structural
capability in P1 delivers value without it.

**Independent Test**: Add a session template to the library without modifying generator logic and confirm
the generator can select it.

**Acceptance Scenarios**:

1. **Given** the library, **When** the plan generator builds a week, **Then** each session originates from a
   template rather than from generator-embedded construction.
2. **Given** a new template is added to the library, **When** a plan is generated, **Then** the template can
   be selected without any change to generator logic.
3. **Given** a template, **When** it is inspected, **Then** its purpose, its training intent, and the
   circumstances it suits are stated alongside its steps.
4. **Given** a template and an athlete's targets, **When** the template is fitted, **Then** the resulting
   session meets the week's load target while preserving the template's structural character.
5. **Given** the library, **When** it is inspected, **Then** templates cover every session type the
   periodization can call for, in every phase.

---

### User Story 4 - Intensities follow the athlete, not the calendar (Priority: P2)

The athlete's threshold improves and they update it at their training log. Every future session
automatically targets the new correct intensities. Nothing in the library or the stored plan needed editing,
and sessions the athlete already completed still mean what they meant at the time.

**Why this priority**: Templates that store absolute intensities become wrong the moment an athlete
improves, and silently misprescribe training. It is P2 because it only manifests once a threshold changes.

**Independent Test**: Change the athlete's threshold and confirm future sessions target new intensities
while completed history is unchanged.

**Acceptance Scenarios**:

1. **Given** a session template, **When** it is stored, **Then** it expresses intensity relative to the
   athlete's thresholds rather than as absolute values.
2. **Given** the athlete's threshold changes, **When** a future session is presented, **Then** it targets
   intensities derived from the new threshold.
3. **Given** the athlete's threshold changes, **When** past completed sessions are examined, **Then** the
   values recorded against them are unchanged.
4. **Given** the athlete trains by heart rate rather than power, **When** sessions are presented, **Then**
   intensities are expressed in the terms that athlete actually uses.

---

### User Story 5 - Plans created before the change still open (Priority: P2)

An athlete has a plan generated by the previous version. After upgrading, that plan still displays, still
matches activities, and still scores adherence. They are not forced to regenerate it and lose their week.

**Why this priority**: Plans are stored as structured documents. A schema change that cannot read what is
already stored destroys the athlete's current training block. It is P2 rather than P1 only because
regeneration is a survivable workaround.

**Independent Test**: Open a plan created under the previous shape and exercise every feature that reads it.

**Acceptance Scenarios**:

1. **Given** a plan stored in the previous shape, **When** it is loaded, **Then** it loads successfully
   without error.
2. **Given** a plan stored in the previous shape, **When** a session from it is displayed or matched,
   **Then** it behaves correctly even though it has no steps.
3. **Given** a plan stored in the previous shape, **When** the athlete generates a new plan, **Then** the
   new one has full structure and the old one is unaffected.
4. **Given** a session with no steps, **When** something requires steps, **Then** it is reported as
   unavailable rather than fabricated.

---

### User Story 6 - Session descriptions are not locked to one language (Priority: P3)

A deployer running the coach in another language sees session descriptions in that language. The
descriptions come from the session's structure and the configured voice, not from text baked into the
generator.

**Why this priority**: An open source project defaulting to English cannot ship a generator that emits
French descriptions. It is P3 because it affects presentation rather than correctness, and only matters
once other people deploy.

**Independent Test**: Configure a different language and confirm session descriptions follow it.

**Acceptance Scenarios**:

1. **Given** a session with steps, **When** a description is needed, **Then** it can be produced from the
   structure rather than retrieved from generator-embedded text.
2. **Given** a configured language, **When** a session is described, **Then** the description is in that
   language.
3. **Given** the change is complete, **When** the stored session is inspected, **Then** it does not carry a
   description fixed to a single language as its only description.

### Edge Cases

- A template is fitted to a load target so low or so high that preserving its structure would produce
  something unrideable, such as efforts shorter than is meaningful or a session longer than the athlete's
  available time.
- The athlete has no measured threshold, so relative intensities cannot be resolved to concrete targets.
- A session's steps sum to a duration that disagrees with the session's stated duration.
- A repeated group specifies zero or one repetition.
- The coach's conversational plan modification shortens or lengthens a session that has structured steps.
- A template references an intensity zone that the athlete's configured zone scheme does not define.
- A stored plan is partially structured, with some sessions having steps and others not.
- A rest day, which is the absence of a session rather than a session of zero length.
- The library contains no template matching what the periodization asked for.
- Two templates are equally suitable, and selection must be repeatable rather than arbitrary.

## Requirements *(mandatory)*

### Functional Requirements

#### Session structure

- **FR-001**: A planned session MUST be expressible as an ordered sequence of steps.
- **FR-002**: Each step MUST state its duration and its intended intensity.
- **FR-003**: Repeated efforts MUST be expressible as a repeated group with a repetition count, rather than
  as duplicated steps.
- **FR-004**: Sessions without internal structure MUST use the same representation as structured ones,
  rather than being represented as a distinct kind of thing.
- **FR-005**: A session's total duration MUST be derived from its steps, so the two cannot disagree.
- **FR-006**: A session's load target MUST be consistent with its steps and their intensities.
- **FR-007**: The structure MUST be rich enough to describe a session to a device that executes it, without
  requiring information the session does not carry.

#### Compatibility

- **FR-008**: The existing summary attributes of a session — its type, its principal intensity, its
  duration, its time-in-zone target, and its load target — MUST remain available and retain their current
  meaning.
- **FR-009**: Summary attributes MUST be derived from the steps rather than stored independently of them.
- **FR-010**: Activity-to-session matching MUST produce the same results after the change as before.
- **FR-011**: Adherence scoring MUST produce the same results after the change as before.
- **FR-012**: Plan modification MUST leave a session internally consistent, with steps and summary in
  agreement.
- **FR-013**: Plans stored in the previous shape MUST continue to load and to work with every feature that
  reads them.
- **FR-014**: Where a session genuinely has no steps, absence MUST be reported rather than fabricated.

#### Session library

- **FR-015**: Session templates MUST live in a library that is readable and editable without modifying plan
  generation logic.
- **FR-016**: Adding a template to the library MUST make it available for selection without a logic change.
- **FR-017**: Each template MUST state its purpose, its training intent, and the circumstances it suits,
  alongside its steps.
- **FR-018**: The plan generator MUST select sessions from the library rather than constructing them
  internally.
- **FR-019**: The library MUST cover every session type the periodization can request, across every phase.
- **FR-020**: Selection between equally suitable templates MUST be repeatable rather than arbitrary.
- **FR-021**: When no template matches a request, the system MUST report it rather than silently
  substituting an unsuitable session.

#### Fitting and intensity

- **FR-022**: Templates MUST express intensity relative to the athlete's thresholds, never as absolute
  values.
- **FR-023**: Fitting a template to a week's load target MUST preserve the template's structural character
  rather than distorting it beyond recognition.
- **FR-024**: Fitting MUST refuse, and report, rather than produce a session that is unrideable or exceeds
  the athlete's stated availability.
- **FR-025**: When the athlete's thresholds change, future sessions MUST target intensities derived from the
  new values while records of completed sessions remain unchanged.
- **FR-026**: Intensities MUST be presentable in the terms the athlete actually trains by, whether power or
  heart rate.
- **FR-027**: When no threshold is known, the system MUST present relative intensities rather than
  fabricating absolute targets.

#### Description

- **FR-028**: A session description MUST be producible from its structure rather than retrieved from text
  embedded in generation logic.
- **FR-029**: Descriptions MUST follow the configured language.
- **FR-030**: A stored session MUST NOT carry text fixed to a single language as its only description.

### Key Entities

- **Step**: One continuous portion of a session, carrying a duration and an intended intensity.
- **Repeated group**: An ordered set of steps performed a stated number of times.
- **Structured session**: A planned session expressed as an ordered sequence of steps and repeated groups,
  from which its summary attributes are derived.
- **Session template**: A reusable session pattern in the library, expressed relatively so it can be fitted
  to any athlete, carrying its purpose and training intent.
- **Session library**: The collection of templates, readable and extensible independently of generation
  logic.
- **Fitting**: Adapting a template to a specific athlete and a specific week's load target while preserving
  its structural character.
- **Relative intensity**: An intensity expressed against the athlete's thresholds rather than in absolute
  units.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of generated sessions are expressed as ordered steps, including sessions with no internal
  structure.
- **SC-002**: A session's stated duration equals the sum of its steps in 100% of generated sessions.
- **SC-003**: Every existing capability that reads planned sessions behaves identically before and after,
  verified across plan display, reminders, matching, adherence scoring, conversational context, and plan
  modification.
- **SC-004**: Activity-to-session matching and adherence scoring produce byte-identical results on a corpus
  of historical activities before and after the change.
- **SC-005**: A plan created under the previous shape loads and works with every feature that reads it.
- **SC-006**: A contributor adds a working session template without modifying any generation logic.
- **SC-007**: The library contains at least one suitable template for every session type in every
  periodization phase, with no gaps.
- **SC-008**: Changing the athlete's threshold changes every future session's absolute targets and changes
  no record of a completed session.
- **SC-009**: A session generated for an athlete training by heart rate expresses its intensities in heart
  rate terms.
- **SC-010**: Session descriptions render in the configured language, with no language-fixed text remaining
  in generation logic.

## Assumptions

- The current planned-session representation is flat: one type, one intensity zone, one duration, one
  time-in-zone target, one load target, and one description. It cannot express a warm-up, a set of efforts
  with recoveries, and a cool-down, which is why this work exists.
- The attributes of a planned session are read in roughly twenty places across the system. The change is
  therefore additive: steps are added, and the existing attributes are retained as values derived from
  them. This keeps existing readers working unchanged and prevents the summary from drifting away from the
  steps, at the cost of some redundancy.
- Plans are already stored as structured documents, so plans created under the previous shape exist and
  must keep working. Regenerating a plan mid-block would cost the athlete their training week.
- Section 11's session library, which is MIT licensed, is the intended starting point for the library's
  content. Its material must be attributed, and its licence preserved. Its structure is adopted; its
  training content is reviewed rather than copied blindly.
- The current generator builds session descriptions from text templates written in French, and the
  description attribute is itself named for that language. An open source project defaulting to English
  cannot keep that, which is why description generation moves to the structure and the configured voice.
- A device-executable session needs steps with durations and intensities. Exactly what a given destination
  requires is a concern of the push feature; this specification's obligation is that the session carries
  enough information to satisfy it.
- Periodization and weekly load targets are unchanged. This work changes how a session is described, not
  how much training is prescribed or when.
- The current implementation is the behavioural baseline for FR-008 through FR-014. Where this spec and the
  running system disagree, the disagreement is a defect in this spec and should be raised rather than
  silently resolved.
