# Feature Specification: intervals.icu as Sole Training Data Source

**Feature Branch**: `002-intervals-icu-provider`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Replace Strava with intervals.icu as the single, mandatory training data
source, detecting newly completed activities by periodic querying rather than inbound push. Consume the
metrics the source already computes rather than recomputing them, capture the wellness data Strava never
exposed, preserve the post-activity engagement loop unchanged, and remove both the Strava path and the
manual session-entry path.

## Scope

**In scope**: connecting to the training data source, detecting and ingesting completed activities,
consuming authoritative metrics, capturing wellness data, importing initial history, preserving the
post-activity loop and plan matching, and removing the two superseded paths.

**Out of scope** (each specified elsewhere): migration to a local embedded datastore, writing planned
workouts to the athlete's calendar, the onboarding rework, and the implementation of readiness and
workload guardrails. This spec captures wellness data; it does not act on it.

**Depends on**: the local embedded database migration, which is built first. That migration establishes the
automatic schema-evolution mechanism this specification's new storage — wellness records, notification
markers, sync state — relies on, and it removes the previous provider's authorization records as part of
its own carry-over. Building this first would mean porting a schema that is changing underneath the port.

**Inherited and not re-argued here**: spec 001 established that the training data source is mandatory,
that manual session entry is removed, that detection is by periodic querying with no inbound endpoint,
and that source-computed metrics are consumed rather than recomputed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The athlete connects their training log by pasting one key (Priority: P1)

The athlete opens their training log's settings, copies their personal API key, pastes it into their
configuration, and starts the system. It confirms the connection and names the athlete it found. There is
no browser redirect, no consent screen, no application to register, no callback address to configure, and
nothing to expose to the internet.

**Why this priority**: This replaces the single largest source of setup friction and is what makes the
self-hosted story credible. Nothing else in this spec can be exercised until a connection exists.

**Independent Test**: On a fresh deployment, supply only a personal key and confirm the system connects and
identifies the athlete.

**Acceptance Scenarios**:

1. **Given** a valid personal key in configuration, **When** the system starts, **Then** it confirms the
   connection and reports which athlete account it is bound to.
2. **Given** an absent, malformed, or rejected key, **When** the system starts, **Then** it refuses to start
   and states which value is wrong and where to obtain a correct one.
3. **Given** secure credential storage is unavailable on the host, **When** the system starts, **Then** it
   refuses to start rather than persisting the key unencrypted.
4. **Given** a working connection whose key is later revoked at the source, **When** the next query fails
   with an authorization error, **Then** the athlete is told the credential is no longer valid and what to
   do, and the system does not silently stop working.

---

### User Story 2 - A completed ride reaches the athlete as a coaching moment (Priority: P1)

The athlete finishes a ride. Their device syncs to their training log as usual. Shortly afterwards, and
without the athlete doing anything, the coach opens a conversation about that ride: it recognises which
planned session it corresponds to, leads with something notable about it, asks how it felt, and then
delivers its read on the session. The experience is indistinguishable from what the athlete had before the
migration.

**Why this priority**: This is the daily engagement loop and the product's main delivered value. It is the
capability most at risk of quiet degradation during a provider migration, which is why it is specified as a
preservation requirement rather than left implicit.

**Independent Test**: Complete an activity, let it reach the training log, and confirm the coach initiates
the full staged exchange with correct, plan-aware content.

**Acceptance Scenarios**:

1. **Given** a newly completed activity appears at the source, **When** the system next queries, **Then**
   the athlete receives an unsolicited notification about it within the documented maximum delay.
2. **Given** an activity is detected, **When** the coach presents it, **Then** the presentation is staged
   rather than delivered as one block, and only one stage raises an audible or vibrating alert.
3. **Given** an activity is detected, **When** the coach presents it, **Then** it includes a selected
   notable aspect of the session and flags a personal best where one occurred.
4. **Given** the athlete has been asked how the session felt, **When** they answer, **Then** the existing
   message is replaced in place by the coach's narrative rather than a new alert being raised.
5. **Given** the athlete never answers, **When** a reasonable interval passes, **Then** the coaching
   feedback is still delivered rather than remaining blocked awaiting an answer.
6. **Given** an activity matches no planned session, **When** the coach presents it, **Then** it is framed
   as unplanned or bonus training and never as an error, a failure, or a missed session.

---

### User Story 3 - The coach's numbers are the athlete's numbers (Priority: P1)

The athlete looks at their training log and sees their training load for a ride, and their fitness, fatigue
and form. They ask the coach the same questions and get the same answers, to the same precision.

**Why this priority**: Equal in weight to the loop above. A coach whose numbers disagree with the athlete's
own training log is not trusted, and every downstream recommendation inherits that distrust.

**Independent Test**: Sample values across several weeks at the source and compare them to what the coach
reports for the same dates.

**Acceptance Scenarios**:

1. **Given** the source reports a training load for an activity, **When** the coach refers to that
   activity's load, **Then** the value matches the source exactly and was not derived locally.
2. **Given** the source reports fitness, fatigue, and form for a date, **When** the coach reports them,
   **Then** the values match the source exactly.
3. **Given** the source holds the athlete's training zones and thresholds, **When** the coach reasons about
   zones or intensity, **Then** it uses the source's values.
4. **Given** the source has not computed a particular metric for an activity, **When** the coach discusses
   that activity, **Then** it states the metric is unavailable rather than estimating a substitute.
5. **Given** the source is unreachable, **When** the athlete asks for their metrics, **Then** the coach
   reports the values it last retrieved together with their age, and does not present them as current.

---

### User Story 4 - A new athlete does not start from zero (Priority: P2)

An athlete connects their training log for the first time. They have been training for years and their
history is already in that log. The coach does not treat them as untrained: it imports enough of their
history that their chronic training load is meaningful from the first conversation, and any plan it builds
reflects the athlete they actually are.

**Why this priority**: Without it, the first weeks of use produce wrong load guidance for an experienced
athlete. It is P2 because the system is usable, if initially miscalibrated, without it.

**Independent Test**: Connect an account with substantial history and verify that chronic load reflects that
history rather than starting near zero.

**Acceptance Scenarios**:

1. **Given** a first connection to an account with existing history, **When** the initial import completes,
   **Then** enough history is present to establish a meaningful chronic training load.
2. **Given** an initial import is interrupted, **When** the system restarts, **Then** the import resumes
   without duplicating what was already imported and without silently leaving gaps.
3. **Given** an initial import is in progress, **When** the athlete interacts with the coach, **Then** they
   are told the history is still loading rather than being given figures based on partial data presented as
   complete.
4. **Given** an account with little or no history, **When** the import completes, **Then** the system
   proceeds with documented conservative assumptions and says so, rather than failing.

---

### User Story 5 - Wellness signals are captured for future use (Priority: P3)

The athlete records heart rate variability, resting heart rate, and sleep in their training log. The system
collects and retains those signals alongside their training, so that later work can act on them.

**Why this priority**: This data is newly available, was never obtainable from the previous source, and is
the prerequisite for readiness guardrails. It is P3 because this spec only captures it; nothing in this
spec changes behaviour based on it.

**Independent Test**: Record wellness entries at the source and confirm the system holds them, correctly
dated, and reports them on request.

**Acceptance Scenarios**:

1. **Given** the athlete has wellness entries at the source, **When** the system refreshes, **Then** those
   entries are retained and associated with the correct dates.
2. **Given** wellness data is missing for a date, **When** the system stores the period, **Then** the
   absence is recorded as unknown rather than as a zero or an interpolated value.
3. **Given** wellness data has been captured, **When** the athlete asks about it, **Then** the coach can
   report it without drawing readiness conclusions that this spec does not authorize.

---

### User Story 6 - The superseded paths are gone (Priority: P3)

A contributor reads the project and finds one way that training data enters the system. There is no second
provider integration lying dormant, no manual entry flow, and no inbound endpoint for activity events.

**Why this priority**: Dead alternative paths mislead contributors, accumulate untested code, and quietly
reintroduce the deployment requirements this migration exists to remove. It is P3 because it delivers no
athlete-visible value.

**Independent Test**: Search the project for the removed capabilities and confirm none remain reachable.

**Acceptance Scenarios**:

1. **Given** the migration is complete, **When** the project is inspected, **Then** no path exists for the
   athlete to enter a completed session by hand.
2. **Given** the migration is complete, **When** the project is inspected, **Then** the previous provider's
   authorization flow, signed-state handling, inbound event endpoint, history import, and connection
   commands are absent.
3. **Given** manual entry is removed, **When** the athlete completes an activity, **Then** they are still
   asked how it felt and that answer still informs the coaching feedback.
4. **Given** the migration is complete, **When** the deployment is inspected, **Then** it accepts no inbound
   connection for activity notification.

### Edge Cases

- The source is unreachable, rate limited, or erroring during a scheduled refresh. The system must retry
  without exhausting its quota and without notifying the athlete about the failure on every attempt.
- The system was stopped for days and many activities accumulated. It must account for all of them without
  sending one alert per activity.
- An activity is edited, renamed, re-analyzed, or has its power data corrected after the athlete was already
  notified. It must not be treated as new.
- An activity is deleted at the source after being ingested. The coaching history must remain coherent.
- Two activities are completed on the same day, or an activity spans midnight in the athlete's timezone.
  Matching to planned sessions must remain correct.
- An activity is not cycling, or is a manual entry at the source with no measured data. The coach must
  handle it without producing a misleading performance report.
- The athlete changes their threshold or zones at the source. Previously stored per-activity values that
  were computed under the old thresholds must not silently change meaning.
- The source returns an activity whose training load it has not yet finished computing. The system must not
  treat an absent value as zero.
- The athlete's clock, the source's timestamps, and the athlete's local timezone disagree. Dates used for
  matching and for daily boundaries must be unambiguous.
- A refresh is still running when the next one is due. Refreshes must not overlap destructively.
- The quota is exhausted. The system must degrade predictably and recover on its own.

## Requirements *(mandatory)*

### Functional Requirements

#### Connection and credentials

- **FR-001**: The system MUST authenticate to the training data source using a personal credential supplied
  by the athlete, requiring no delegated authorization flow, no registered application, and no inbound
  callback.
- **FR-002**: The system MUST verify the credential at startup and identify the bound athlete account.
- **FR-003**: The system MUST refuse to start when the credential is absent, malformed, or rejected, naming
  the offending value and how to obtain a valid one.
- **FR-004**: The system MUST store the credential encrypted at rest, and MUST refuse to persist it rather
  than storing it unencrypted when secure storage is unavailable.
- **FR-005**: The system MUST detect a credential that stops being accepted during operation and inform the
  athlete, rather than degrading into silent inactivity.

#### Detection and ingestion

- **FR-006**: The system MUST detect newly completed activities by querying the source on a recurring
  schedule, and MUST NOT expose any inbound endpoint for this purpose.
- **FR-007**: The recurring interval MUST default to five minutes, MUST be configurable by the operator,
  and MUST be enforced against a documented minimum that protects the source's published quota.
- **FR-008**: The system MUST stay within the source's published request quotas under normal operation,
  including during initial history import.
- **FR-009**: The system MUST notify the athlete exactly once per activity, with no duplicates and no
  omissions, and this MUST hold across restarts.
- **FR-010**: The system MUST NOT treat a previously ingested activity as new when it is subsequently
  edited, renamed, or re-analyzed at the source.
- **FR-011**: The system MUST bound the number of notifications produced when many activities are detected
  at once, while still accounting for every activity in the athlete's training record.
- **FR-012**: The system MUST NOT mark an activity as reported until the notification has actually been
  delivered.
- **FR-013**: The system MUST tolerate transient source failures with retries that neither exhaust the
  quota nor alert the athlete on every attempt.
- **FR-014**: Concurrent or overlapping refreshes MUST NOT produce duplicate ingestion or corrupt state.

#### Metric authority

- **FR-015**: The system MUST consume, unmodified, the metrics the source computes: per-activity training
  load, fitness, fatigue, form, power and heart-rate zones, and threshold values.
- **FR-016**: The system MUST NOT recompute locally any metric obtained under FR-015.
- **FR-017**: The system MUST remove the local computation of training load and of training zones, which no
  longer has any caller now that a training data source is mandatory and manual entry is removed.
- **FR-018**: The system MUST retain local deterministic computation for outcomes the source does not
  provide: periodization, plan generation, plan modification, activity-to-session matching, adherence
  scoring, and projected future form.
- **FR-019**: The language model MUST NOT compute any training load value.
- **FR-020**: When a metric is absent at the source, the system MUST represent it as unknown and MUST NOT
  substitute a computed or default value.
- **FR-021**: When source data cannot be refreshed, the system MUST report the age of the data it is using
  and MUST NOT present it as current.
- **FR-022**: Per-activity values captured under the thresholds in force at the time MUST remain stable when
  the athlete later changes those thresholds.

#### Wellness capture

- **FR-023**: The system MUST retrieve and retain the wellness signals the source exposes, including heart
  rate variability, resting heart rate, and sleep, associated with their correct dates.
- **FR-024**: The system MUST record a missing wellness value as unknown rather than as zero or an
  interpolated value.
- **FR-025**: The system MUST NOT derive readiness conclusions from wellness data within this feature;
  capture is in scope, interpretation is not.

#### Initial history import

- **FR-026**: On first connection, the system MUST import enough activity history to establish a meaningful
  chronic training load rather than starting the athlete from zero.
- **FR-027**: An interrupted import MUST resume without duplicating already-imported activities and without
  leaving undetected gaps.
- **FR-028**: While an import is in progress, the system MUST indicate that history is incomplete rather
  than presenting partial figures as complete.
- **FR-029**: When an account has little or no history, the system MUST proceed with documented conservative
  assumptions and disclose that it has done so.

#### Preserved behaviour

- **FR-030**: The staged post-activity presentation MUST be preserved, including its ordering, its selection
  of a notable aspect, its personal-best detection, and its restraint in raising only one alert.
- **FR-031**: Capture of the athlete's perceived exertion MUST be preserved and MUST continue to inform the
  coaching feedback, notwithstanding the removal of manual session entry.
- **FR-032**: Coaching feedback MUST still be delivered when the athlete does not report perceived exertion.
- **FR-033**: Activity-to-session matching MUST preserve its current behaviour: a window of plus or minus
  two days bounded by the training week, a hundred-point score, prevention of two activities claiming the
  same planned session, and presentation as bonus training when every candidate is already claimed.
- **FR-034**: Training completed outside the plan MUST never be presented as an error or a failure.
- **FR-034a**: The weekly review of adherence and training load MUST continue to produce correct results
  once load values come from the source rather than from local computation. Because it consumes those
  values and the stored adherence record, it cannot be assumed unaffected and MUST be verified explicitly.
- **FR-034b**: The daily session reminder MUST continue to function unchanged.
- **FR-034c**: Changes to load and metric handling MUST ship with tests, per the project's constitutional
  requirement that engine changes be test-covered. Removing superseded local calculations MUST also remove
  the tests that covered only them, without weakening coverage of what remains.

#### Removals

- **FR-035**: Manual entry of completed sessions MUST be removed, including its command, its duration
  prompt, and its associated conversational states.
- **FR-036**: The previous provider's integration MUST be removed in full, including its authorization flow,
  signed-state handling, inbound event endpoint, history import, and connection and status commands.
- **FR-037**: Capabilities whose sole purpose was to support a removed path MUST be removed with it, while
  capabilities that merely shared an implementation MUST be preserved per FR-031.

#### Structural quality

- **FR-038**: Assembly of post-activity context MUST live in a layer independent of the bot framework, so
  that it is testable without simulating a conversation.
- **FR-039**: Event handlers MUST NOT access stored data directly; data access MUST go through the
  designated data-access layer.
- **FR-040**: Personal-best detection MUST accept the data it needs directly, without requiring callers to
  fabricate a stand-in object to satisfy its interface.
- **FR-041**: The surviving post-activity path MUST NOT contain a value whose availability depends on a
  condition being duplicated identically in more than one place.

### Key Entities

- **Training data source**: The external service holding the athlete's training log, computing authoritative
  load and fitness metrics, and exposing wellness records. Mandatory.
- **Personal credential**: The athlete's own key granting access to their own data at the source. Encrypted
  at rest, never shared, never brokered.
- **Activity record**: One completed training session as retrieved from the source, carrying the metrics the
  source computed and the identity needed to recognise it again.
- **Reported marker**: The durable record that a given activity has already been presented to the athlete,
  which must survive restarts.
- **Wellness record**: A dated set of recovery signals, any of which may be unknown.
- **Planned session**: An entry in the locally generated plan, to which an activity record may be matched.
- **Post-activity context**: The assembled set of facts about a completed activity and its surroundings,
  used to produce coaching feedback.
- **Sync state**: What has been retrieved so far, how far back history extends, and when the last successful
  refresh occurred.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete connects their training log by supplying one credential, with no browser-based
  authorization step and no inbound endpoint, in under 2 minutes.
- **SC-002**: 100% of newly completed activities produce exactly one notification, verified across at least
  twenty activities including at least two system restarts and one extended outage.
- **SC-003**: The athlete is notified within the documented maximum delay of an activity appearing at the
  source, measured across a representative sample.
- **SC-004**: Every metric the coach reports that the source also reports matches it exactly, sampled across
  at least four weeks of history.
- **SC-005**: Steady-state operation consumes no more than 10% of the source's published daily request
  quota, leaving ample margin for imports and retries.
- **SC-006**: The complete post-activity exchange is present after migration with no stage, no notable
  aspect, no personal-best detection, and no perceived-exertion step lost, verified against the
  pre-migration behaviour.
- **SC-007**: An athlete with multi-year history has a chronic training load consistent with that history at
  the end of first-time setup, rather than one near zero.
- **SC-008**: Wellness signals present at the source for a period are retained for that period, with missing
  values distinguishable from zero values.
- **SC-009**: No path for manual session entry and no artefact of the previous provider integration remains
  reachable in the shipped project.
- **SC-010**: Editing an already-reported activity at the source produces no second notification, verified
  by editing at least three previously reported activities.

## Assumptions

- The training data source is intervals.icu. It authenticates with a personal API key over basic
  authentication, publishes quotas of roughly 5,000 requests per day, 2,500 per rolling fifteen minutes, and
  10 per second, and exposes activities, calendar events, wellness records, and athlete settings.
- A five-minute interval is chosen as the default because it consumes roughly 6% of the daily quota while
  bounding notification delay to five minutes. It is a product decision, revisable without altering any
  requirement other than FR-007.
- The delay the athlete perceives includes their own device-to-source synchronization, which this system
  neither controls nor guarantees. The documented maximum delay is measured from the moment an activity is
  visible at the source.
- Inbound push was evaluated and rejected in spec 001 and is not reconsidered here. Should it ever be
  revisited, it is additive and invalidates nothing specified in this document.
- The athlete's devices already synchronize to the source. Making this system the athlete's primary training
  log is explicitly not a goal.
- Activities reaching the source by way of the previous provider are understood to behave differently from
  natively synchronized ones. Direct synchronization is assumed and is the documented recommendation.
- The current implementation is the behavioural baseline for FR-030 through FR-034. Where this spec and the
  running system disagree, the disagreement is a defect in this spec and should be raised rather than
  silently resolved in either direction.
- No third party is running this software today, so no migration path is required for existing deployments
  holding data from the previous provider.
- Structural requirements FR-038 through FR-041 correspond to specific defects identified by audit of the
  surviving code path, and are stated as outcomes so they remain verifiable without prescribing a design.
