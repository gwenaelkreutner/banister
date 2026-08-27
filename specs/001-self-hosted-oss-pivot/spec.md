# Feature Specification: Self-Hosted Open Source Pivot

**Feature Branch**: `001-self-hosted-oss-pivot`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: Pivot Banister from a personal cloud-coupled project (Supabase + Strava) to an
open source, single-user, self-hosted product that anyone can deploy on their own VPS. This spec
establishes the deployment target and product principles; the technical migrations (local database,
intervals.icu provider, workout push, onboarding rework, guardrail implementation) each get their own spec.

## Scope

**In scope**: deployment target, data ownership and privacy posture, the metric-authority principle,
guardrail requirements, and open source release requirements.

**Out of scope** (each gets a dedicated spec): the implementation detail of the local database migration,
the intervals.icu provider implementation, pushing planned workouts to the athlete's calendar, the
onboarding rework, and the implementation of the guardrails required here.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A stranger deploys Banister on their own server (Priority: P1)

A cyclist finds the project, has their own server and an intervals.icu account, and wants to run their own
coach. They clone the repository, copy the example configuration file, fill in four values (their bot token,
their own chat identifier, their intervals.icu key, and their model provider key), and start the stack with a
single command. Within minutes they are chatting with their own coach in Telegram. They never register a
domain name, never configure a reverse proxy, never obtain a TLS certificate, and never create an account
with the project.

**Why this priority**: This is the entire point of the pivot. If a competent stranger cannot get from clone
to a working coach without external infrastructure, the project is not self-hostable and nothing else in
this spec matters.

**Independent Test**: On a clean machine with no prior project state, follow only the README, and reach a
responding bot. Deliverable value: a working self-hosted coach.

**Acceptance Scenarios**:

1. **Given** a clean machine with a container runtime and no other project state, **When** the operator
   follows only the documented install steps, **Then** the coach responds in Telegram without any public
   domain name, inbound webhook, reverse proxy, or TLS certificate being configured.
2. **Given** a required configuration value is missing or malformed, **When** the operator starts the stack,
   **Then** startup fails immediately with a message naming the specific missing value and how to obtain it,
   rather than starting in a partially working state.
3. **Given** a running deployment, **When** the operator stops the stack and deletes the single documented
   data directory, **Then** no athlete data produced by the application remains anywhere on the machine.
4. **Given** the operator has not connected an intervals.icu account, **When** they start the stack,
   **Then** startup fails with a message explaining that intervals.icu is required, because the coach cannot
   function without a training data source.

---

### User Story 2 - The athlete's numbers match what they see on intervals.icu (Priority: P1)

The athlete opens intervals.icu in a browser and sees their Fitness, Fatigue and Form. They then ask the
coach in Telegram how their form is. The coach reports the same values. When the coach suggests a session
or explains a decision, the reasoning refers to those same numbers.

**Why this priority**: Divergence between the coach's numbers and the athlete's own training log destroys
trust in every recommendation the coach makes. This is equally foundational to P1 above and is the reason
the metric-authority principle exists.

**Independent Test**: Compare the values the coach reports against the values visible on intervals.icu for
the same date, and verify they agree.

**Acceptance Scenarios**:

1. **Given** the training data source reports a fitness, fatigue, form, or training load value for a date,
   **When** the coach reports that value to the athlete, **Then** the reported value equals the source
   value and is not recomputed locally.
2. **Given** the training data source reports the athlete's training zones, **When** the coach reasons about
   or displays zones, **Then** it uses the source's zones rather than deriving its own.
3. **Given** the training data source is unreachable, **When** the athlete asks for their metrics,
   **Then** the coach states that the data is stale, reports the date of the last successful sync, and does
   not silently substitute a locally computed estimate.

---

### User Story 3 - The athlete is told about a ride shortly after finishing it (Priority: P2)

The athlete finishes a ride. Their device syncs to their training log as it always has. Without the athlete
asking for anything, the coach reaches out in Telegram: it recognises the ride, tells them how it went
against what was planned, asks how it felt, and then gives them a short read on the session in the coach's
voice. The athlete did not open the app, did not log anything, and did not request a report.

**Why this priority**: This is the product's daily engagement loop and the moment the coach delivers most of
its perceived value. A coach that only answers when spoken to is a reference tool, not a coach. It is P2
rather than P1 because it depends on the data source connection established in the P1 stories.

**Independent Test**: Complete an activity, let it reach the training log, and verify the coach initiates
contact with a correct, plan-aware account of that activity without any prompting.

**Acceptance Scenarios**:

1. **Given** a new completed activity appears in the athlete's training log, **When** the system next
   refreshes, **Then** the coach sends the athlete an unsolicited notification about that activity within
   the documented maximum delay.
2. **Given** an activity corresponds to a planned session, **When** the coach notifies the athlete,
   **Then** the notification states how the activity compared to what was planned.
3. **Given** an activity does not correspond to any planned session, **When** the coach notifies the
   athlete, **Then** it is presented as unplanned or bonus training rather than as a failure or an error.
4. **Given** the athlete has been notified about an activity, **When** the same activity is seen again in a
   later refresh, **Then** no duplicate notification is sent.
5. **Given** the athlete responds to the notification with how the session felt, **When** the coach replies,
   **Then** the reply reflects both the recorded data and the athlete's stated perception.
6. **Given** several activities appeared since the last refresh, **When** the coach notifies the athlete,
   **Then** each is accounted for without flooding the athlete with an unbounded burst of messages.

---

### User Story 4 - The athlete trusts the coach not to act behind their back (Priority: P2)

The coach proposes a training week. Nothing appears in the athlete's training calendar until the athlete
explicitly approves it. The athlete knows exactly where their data goes and can turn off anything that
leaves the machine.

**Why this priority**: Writing to someone's calendar or transmitting their data without consent is the
fastest way to lose a self-hosting audience, which self-selects for privacy sensitivity. It is P2 rather
than P1 because the coach still delivers value in a read-only capacity before write features exist.

**Independent Test**: Attempt every outbound-effect path and confirm each one is either gated behind an
explicit approval or documented and disableable.

**Acceptance Scenarios**:

1. **Given** the coach has generated a training plan, **When** no explicit approval has been given by the
   athlete, **Then** nothing is written to the athlete's training calendar.
2. **Given** the athlete has approved a proposed set of sessions, **When** the write is performed, **Then**
   only the approved sessions are written and the athlete is told what was written.
3. **Given** a message arrives from any account other than the configured owner, **When** the system
   processes it, **Then** it is ignored without any reply that would reveal the deployment exists.
4. **Given** the operator sets the documented opt-out variable, **When** the application runs, **Then** it
   makes no outbound request other than to the athlete's chosen model provider, the training data source,
   and the messaging platform.

---

### User Story 5 - A contributor can understand and extend the project (Priority: P3)

A developer discovers the project, reads the repository, understands the architecture and the rules that
govern it, runs the tests locally, and opens a pull request that is validated automatically.

**Why this priority**: Necessary for the project to be genuinely open source rather than merely public, but
it delivers no value to an athlete until the deployment story above works.

**Independent Test**: A developer with no prior exposure follows the contributor documentation and gets a
passing local test run and a green automated check on a pull request.

**Acceptance Scenarios**:

1. **Given** a fresh clone, **When** a contributor follows the contributing guide, **Then** they can run the
   full test suite locally and it passes.
2. **Given** a pull request is opened, **When** automated checks run, **Then** tests and linting are executed
   and the result is reported on the pull request.
3. **Given** the project reuses material from third-party open source projects, **When** a reader inspects
   the repository, **Then** the origin and licence of that material are attributed.

### Edge Cases

- The training data source is unreachable, rate limited, or returns an error during a scheduled refresh.
  The coach must continue to answer using the last known data, labelled with its age, rather than failing
  or fabricating current values.
- The athlete's credentials for the training data source are revoked or expire. The system must tell the
  athlete which credential is broken and how to replace it, rather than degrading silently.
- Secure storage for credentials is unavailable on the host. The system must refuse to start rather than
  write credentials in the clear.
- The athlete asks a question that would require a metric the source has not computed. The coach must say
  the metric is unavailable rather than inventing or approximating it.
- The athlete's account has no activity history at all. Plan generation must either succeed with documented
  conservative defaults or explain precisely what is missing.
- Two instances are started against the same data directory. The second must fail clearly rather than
  corrupting shared state.
- The system was stopped for an extended period and many activities accumulated. On restart it must not
  emit one notification per missed activity, and must not silently forget them either.
- An activity is edited, renamed, or deleted in the training log after the athlete was already notified
  about it. The system must not treat the edited activity as a new one.
- An activity arrives that is not cycling, or carries no usable data at all. The coach must handle it
  without producing a misleading performance report.
- The athlete never answers the question about how the session felt. The feedback must still be delivered
  rather than remaining blocked awaiting a response.
- A notification is generated while the messaging platform is unreachable. The activity must not be marked
  as reported until delivery actually succeeds.
- The athlete revokes calendar write approval after previously granting it. Previously written sessions must
  be addressed by a documented, predictable behaviour.

## Requirements *(mandatory)*

### Functional Requirements

#### Deployment and operation

- **FR-001**: The system MUST be deployable from a clean host using only a container orchestration command
  and a single configuration file, with no manual database provisioning step.
- **FR-002**: The system MUST operate without any inbound network connection from the public internet,
  requiring no public domain, reverse proxy, inbound webhook, or TLS certificate.
- **FR-003**: The system MUST persist all state in an embedded local datastore that requires no separately
  administered database service.
- **FR-004**: The system MUST store all athlete-derived data beneath a single documented directory, such
  that deleting that directory removes all athlete data the application produced.
- **FR-005**: The system MUST validate all required configuration at startup and refuse to start with a
  message naming each missing or malformed value.
- **FR-006**: The system MUST allow the operator to supply their own model provider credentials and MUST NOT
  require or broker any account with the project.

#### Metric authority

- **FR-007**: The system MUST treat the connected training data source as the sole authority for any metric
  that source computes, including training load, fitness, fatigue, form, and training zones.
- **FR-008**: The system MUST NOT recompute locally any metric obtained from the training data source.
- **FR-009**: The system MUST retain local deterministic computation for outcomes the training data source
  does not provide, specifically: periodization, plan generation, plan modification, matching completed
  activities to planned sessions, adherence scoring, and projected future form.
- **FR-010**: The system MUST remove local computation of metrics that the training data source now supplies
  authoritatively, since a connected training data source is mandatory and no unconnected mode exists.
- **FR-011**: The language model MUST NOT compute any training load value; it may only narrate, explain, and
  converse over values produced deterministically.
- **FR-012**: When source data cannot be refreshed, the system MUST report the age of the data it is using
  and MUST NOT substitute an estimate presented as current.

#### Capability preservation

The pivot is a migration of an already working product, not a rebuild. These requirements exist so that no
capability the athlete relies on today disappears silently because no requirement named it.

- **FR-P01**: The system MUST continue to generate a structured, periodized training plan for the athlete.
- **FR-P02**: The athlete MUST be able to view their plan, both the current week and any other week of it.
- **FR-P03**: The athlete MUST be able to see their current fitness, fatigue, and form on demand.
- **FR-P04**: The system MUST continue to produce a weekly review of adherence and training load, both on
  demand and delivered automatically on a recurring schedule.
- **FR-P05**: The system MUST continue to send the athlete a reminder of the day's planned session at a time
  the athlete controls, and MUST stay silent on rest days.
- **FR-P06**: The athlete MUST be able to converse freely with the coach and have that conversation informed
  by their plan, recent training, and current metrics.
- **FR-P07**: When conversation leads the coach to propose a change to the plan, the athlete MUST be able to
  accept or reject that change explicitly, and nothing MUST change without acceptance.
- **FR-P08**: On first connecting a training data source, the system MUST import sufficient activity history
  to establish meaningful chronic training load, rather than starting the athlete from zero.
- **FR-P09**: The system MUST retain the ability to record the athlete's perceived exertion for a completed
  activity, and MUST incorporate it into feedback. Removing manual session entry MUST NOT remove perceived
  exertion capture, which serves the post-activity loop rather than manual logging.
- **FR-P10**: The system MUST preserve its offline plan-quality evaluation capability, so that changes to
  plan generation remain measurable.
- **FR-P11**: The coach's voice MUST remain configurable rather than hardcoded, so that a deployer can adapt
  tone and language without modifying application logic.

#### Deliberate removals

- **FR-R01**: Manual entry of completed sessions MUST be removed. All completed training arrives through the
  training data source. This is a deliberate scope reduction, not an omission.
- **FR-R02**: Any capability whose only purpose was to support manual entry MUST be removed with it, while
  capabilities that merely share an implementation with it MUST be preserved per FR-P09.

#### Proactive notification

- **FR-N01**: The system MUST detect newly completed activities appearing in the athlete's training log
  without the athlete taking any action.
- **FR-N02**: The system MUST notify the athlete of a newly detected activity without being prompted, within
  a documented maximum delay, and that delay MUST be stated in operator-facing documentation.
- **FR-N02a**: Detection MUST be performed by periodically querying the training data source. The system
  MUST NOT expose any inbound endpoint for this purpose, and MUST NOT require the deployer to register an
  application with, or obtain elevated authorization from, the training data source.
- **FR-N03**: The system MUST notify the athlete exactly once per activity, and MUST NOT re-notify for an
  activity already reported, including across restarts.
- **FR-N04**: The notification MUST state how the activity related to the training plan, distinguishing a
  completed planned session, a session completed differently than planned, and unplanned training.
- **FR-N05**: The system MUST invite the athlete to record how the session felt, and MUST incorporate that
  response into the feedback it subsequently gives.
- **FR-N06**: The system MUST bound the number of messages produced when multiple activities are detected in
  a single refresh, so that a backlog cannot flood the athlete.
- **FR-N07**: The athlete MUST be able to disable proactive notifications without losing the ability to ask
  for the same information on demand.
- **FR-N08**: The system MUST NOT present unplanned or below-target training as an error or failure state.

#### Security and privacy

- **FR-013**: The system MUST encrypt stored credentials at rest, and MUST refuse to persist a credential
  rather than writing it unencrypted when secure storage is unavailable.
- **FR-014**: The system MUST silently ignore all messages originating from any account other than the
  single configured owner, without emitting a response.
- **FR-015**: The system MUST NOT transmit athlete data to any destination other than the operator's chosen
  model provider and the connected training data source.
- **FR-016**: The system MUST NOT collect analytics or usage telemetry.
- **FR-017**: If a version-check request is made, it MUST contain no athlete data or credentials and MUST be
  disableable by a documented configuration value.
- **FR-018**: The system MUST document, in operator-facing documentation, every destination athlete data is
  sent to and what is sent.

#### Guardrails (requirements here; implementation specified separately)

- **FR-019**: The system MUST NOT write to the athlete's training calendar without explicit prior approval
  from the athlete for the specific content being written.
- **FR-020**: The system MUST evaluate acute-to-chronic workload ratio and surface it when recommending
  changes in training load.
- **FR-021**: The system MUST evaluate athlete readiness from the wellness signals available from the
  training data source, and MUST apply explicit documented thresholds: a heart rate variability drop
  exceeding 20% below baseline directs an easy day, and a resting heart rate elevated 5 bpm or more above
  baseline raises a fatigue signal.
- **FR-022**: The system MUST apply a documented validation checklist before delivering any coaching
  response, verifying that required data was retrieved, that stated values match retrieved values, and that
  the response respects the metric-authority rule.
- **FR-023**: The system MUST present a disclaimer stating it is neither a physician nor a certified coach,
  and that a proposed session is a suggestion rather than a prescription, at first run and in user-facing
  documentation.

#### Open source release

- **FR-024**: The repository MUST carry an explicit open source licence.
- **FR-025**: The repository MUST provide installation documentation sufficient for a competent stranger to
  reach a working deployment without assistance.
- **FR-026**: The repository MUST provide contributor documentation covering local setup, running tests, and
  the project's governing principles.
- **FR-027**: The repository MUST run automated tests and linting on proposed changes.
- **FR-028**: The repository MUST attribute the third-party open source projects whose material it reuses,
  including their licences.

### Key Entities

- **Operator**: The person who deploys and administers the instance. Holds credentials and configuration.
  In this product the operator and the athlete are the same person, but their concerns differ.
- **Athlete**: The single person the coach serves. Owns the training data and grants or withholds approval
  for calendar writes.
- **Training data source**: The external service holding the athlete's training log and computing
  authoritative metrics. Mandatory; the product does not function without it.
- **Model provider**: The external language model service the operator chooses and pays for directly.
- **Data directory**: The single local location containing all athlete-derived state; deleting it removes
  all such data.
- **Coaching artifacts**: Plans, proposed sessions, adherence results, and projections produced locally and
  not sourced from the training data source.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A competent operator unfamiliar with the project reaches a responding coach in under 15
  minutes from clone, following only the written documentation.
- **SC-002**: Deployment requires zero external infrastructure beyond the host itself: no domain name, no
  certificate, no reverse proxy, and no separately administered database.
- **SC-003**: Every metric the coach reports that the training data source also reports matches it exactly,
  verified across a sample spanning at least four weeks of the athlete's history.
- **SC-004**: Deleting the documented data directory removes 100% of athlete data the application produced,
  verified by inspection of the host.
- **SC-005**: No athlete data reaches any destination not named in the operator documentation, verified by
  observing outbound traffic during a representative session.
- **SC-006**: Zero calendar writes occur without a recorded explicit approval, verified across all paths
  that can produce a write.
- **SC-007**: 100% of newly completed activities produce exactly one unsolicited notification, with no
  duplicates and no omissions, verified across at least twenty activities including restarts of the system.
- **SC-008**: The athlete is notified of a completed activity within the documented maximum delay of it
  appearing in their training log, measured across a representative sample of activities.
- **SC-009**: Every capability enumerated in FR-P01 through FR-P11 is demonstrably available after the
  migration, verified by exercising each one against a real deployment.
- **SC-010**: A contributor with no prior exposure obtains a passing local test run by following the
  contributor documentation alone.
- **SC-011**: Messages from non-owner accounts produce no response of any kind, verified by attempting
  contact from an unauthorized account.

## Assumptions

- The athlete already has, or is willing to create, an intervals.icu account, and their devices already sync
  to it. Making the coach the athlete's primary training log is explicitly not a goal.
- Manual session logging is removed entirely. The athlete cannot enter sessions by hand; all completed
  training arrives through the training data source. This is a deliberate reduction in scope.
- Because a training data source is mandatory, no "unconnected" or "offline-first" operating mode exists,
  and the local calculations that previously supported such a mode are removed rather than retained as
  fallbacks.
- A single athlete per deployment. Multi-tenancy, shared instances, and coach-managing-multiple-athletes are
  out of scope and are not design constraints.
- The operator is technically capable of running a container stack and editing a configuration file, but is
  not assumed to be able to administer a database, a web server, or a certificate.
- Proactive notification is driven by periodic refresh rather than an inbound push from the training data
  source, because accepting an inbound push would reintroduce the public endpoint that FR-002 exists to
  eliminate. The athlete therefore accepts a bounded delay between finishing a ride and being notified,
  in exchange for a deployment that needs no domain, certificate, or reverse proxy. The delay is a product
  decision to be fixed in the provider spec; published rate limits permit a frequent refresh comfortably.
- The athlete's activities reach the training log through their own device synchronization, which may
  itself add delay outside this system's control and outside its notification guarantee.
- Inbound push from the training data source was evaluated and deliberately rejected, even though that
  source does publish activity webhooks. It requires registering an application rather than using a
  personal key, it is documented as not firing for activities that arrive via Strava, and it is itself
  delayed to consolidate events — so it trades a real configuration burden for a marginal latency gain.
  Periodic querying is therefore the only detection mechanism, not merely the default one. Should this be
  revisited later, it is an additive change and does not invalidate anything specified here.
- The existing product's behaviour is the baseline for FR-P01 through FR-P11. Where this spec and the
  current implementation disagree, the disagreement is a defect in this spec and should be raised rather
  than silently resolved in either direction.
- The operator pays their model provider directly; the project neither resells nor brokers model access.
- The athlete's own device-to-intervals.icu synchronization is outside this system's responsibility.
- Existing deployments are the author's own. No migration path for third-party existing installations is
  required, since the project has no external users yet.
