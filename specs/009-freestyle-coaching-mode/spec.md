# Feature Specification: Freestyle Coaching Mode

**Feature Branch**: `009-freestyle-coaching-mode`

**Created**: 2026-09-17

**Status**: Draft

**Input**: User description: Today the coach is only useful when the athlete has an active training plan
built around a target goal and date. The athlete wants a second way to use it: connected to their
intervals.icu account, reacting to every activity they publish, but with no target race or periodization —
just "I feel like training today, suggest me something that fits my current form and what I like to do."
The athlete wants to switch between this "freestyle mode" and the existing "goal mode" whenever their
situation changes, without losing history.

## Scope

**In scope**: a mode where the athlete has no target goal/date and instead gets on-demand session
suggestions based on current fitness and declared preferences; switching between this mode and the existing
goal-based plan mode; keeping post-activity feedback and guardrail signals working the same way regardless
of mode.

**Out of scope**: changing how goal mode itself generates or periodizes a plan; changing the content of the
session library; any new proactive daily reminder for freestyle mode (see Assumptions) — freestyle mode is
reactive (on request) and post-activity, not scheduled.

**Depends on**: the existing session library and session-selection logic, the existing fitness/wellness
data pipeline, and the existing goal-setting flow (`/goal`) that freestyle mode switches into.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask for a session with no goal in place (Priority: P1)

The athlete has never set a target race, or has just left one behind, and has no active training plan. They
tell the coach they feel like training and ask what to do today. Instead of the coach having nothing useful
to say (today's behavior without a plan), it looks at their current fitness and recent training load and
proposes one concrete session — type, target duration, target zone — that fits where they are right now, and
leans on what it already knows they like to do when more than one type of session would fit equally well.

**Why this priority**: This is the core gap. Without it, the app has nothing to offer an athlete who isn't
mid-buildup to a race, which is a large share of real training time (off-season, maintenance, recovery from
injury, "just riding").

**Independent Test**: With no active plan and a connected account with fitness history, ask the coach for a
session suggestion in chat and receive one concrete, fitness-appropriate session.

**Acceptance Scenarios**:

1. **Given** an athlete with no active training plan and recent fitness data, **When** they ask the coach
   for a session, **Then** the coach proposes one session with a type, duration, and target zone consistent
   with their current CTL/ATL/TSB and recent training load.
2. **Given** an athlete who has told the coach (via existing coach memory) that they dislike a certain
   session type, **When** more than one session type would otherwise fit their current form equally well,
   **Then** the coach does not propose the disliked type.
3. **Given** an athlete who trained hard yesterday, **When** they ask for a session today, **Then** the
   coach does not propose another hard session without accounting for that recent load.

---

### User Story 2 - Switch between freestyle and goal mode (Priority: P2)

An athlete using freestyle mode signs up for a race and wants a real build-up plan; separately, an athlete
finishing a race wants to stop following a plan and go back to riding freely. In both directions, they can
tell the coach to switch modes, and their past activities, session logs, and coach memory are unaffected.

**Why this priority**: Without a clean switch, athletes are stuck picking one mode at signup, which does not
match how training actually goes (goals start and end; off-seasons happen).

**Independent Test**: From freestyle mode, provide a goal and date and confirm a training plan is generated
and the coach starts giving plan-based guidance; separately, from goal mode, request to stop following the
plan and confirm the coach switches to on-demand suggestions while past data remains queryable.

**Acceptance Scenarios**:

1. **Given** an athlete in freestyle mode, **When** they set a goal and target date, **Then** the coach
   generates a training plan and subsequent behavior matches today's goal-mode behavior.
2. **Given** an athlete with an active plan, **When** they ask to drop the goal and go back to freestyle,
   **Then** the plan stops driving suggestions, past session logs and activities remain intact, and the
   coach starts giving on-demand suggestions instead.
3. **Given** an athlete who just switched modes, **When** they check their history (past sessions,
   activities, coach memory), **Then** nothing from before the switch is missing or altered.

---

### User Story 3 - Post-activity feedback keeps working without a plan (Priority: P3)

An athlete in freestyle mode publishes a ride to intervals.icu, the same way they would in goal mode. They
still want the coach to notice it and give feedback, even though the ride was not matched against a planned
session because there is no plan.

**Why this priority**: This is what makes freestyle mode feel like the same coach rather than a stripped-down
fallback; it is lower priority than P1/P2 because the underlying activity-detection pipeline already exists
and mainly needs to keep working when there is no plan to match against, rather than being built new.

**Independent Test**: While in freestyle mode, publish an activity to the connected account and confirm the
coach sends feedback on it without referencing a planned session that does not exist.

**Acceptance Scenarios**:

1. **Given** an athlete in freestyle mode, **When** they publish a new activity, **Then** the coach detects
   it and sends feedback the same way it does in goal mode, without claiming it matched or missed a planned
   session.
2. **Given** an athlete in freestyle mode whose recent training pattern would trigger a guardrail signal
   (e.g., load rising too fast, insufficient recovery), **When** that pattern occurs, **Then** the coach
   surfaces the same guardrail signal it would in goal mode.

---

### Edge Cases

- What happens when an athlete in freestyle mode asks for a session but has no fitness history at all yet
  (brand-new connection, no wellness data ingested)? The coach must say plainly that it doesn't have enough
  data yet, rather than guessing a session.
- How does the system handle an athlete asking for a session twice in the same day? It should not be
  required to propose something different the second time, but it must still reflect anything that changed
  (e.g., an activity logged between the two requests).
- What happens to a plan's calendar publication (existing publish/unpublish feature) when the athlete
  switches from goal mode to freestyle mode mid-plan? Resolved: all future published entries are
  automatically withdrawn as part of the switch (see FR-013).
- What happens if the athlete asks for a session while already mid-plan in goal mode (freestyle-style
  request without switching modes)? Out of scope for this feature — goal mode's existing behavior is
  unchanged; the athlete must switch to freestyle mode to get on-demand suggestions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a coaching mode, called freestyle mode, in which the athlete has no
  active target goal, target date, or training plan.
- **FR-002**: In freestyle mode, when the athlete requests a session, the system MUST propose exactly one
  concrete session (type, target duration, target zone) selected without reference to a periodization phase,
  since no plan or phase exists in this mode.
- **FR-003**: The session proposed in freestyle mode MUST be consistent with the athlete's current fitness
  (most recent CTL/ATL/TSB and recent training load), using the same authoritative fitness data the rest of
  the system already uses — never a value invented for the occasion.
- **FR-004**: When the athlete's declared preferences (existing coach memory) would rule out or favor a
  session type, the proposal in freestyle mode MUST respect that preference.
- **FR-005**: The system MUST let the athlete switch from freestyle mode into goal mode by providing a
  target goal and date, at which point the existing plan-generation behavior takes over unchanged.
- **FR-006**: The system MUST let the athlete switch from goal mode into freestyle mode, deactivating the
  current plan as the source of guidance while retaining all of the athlete's history (activities, session
  logs, coach memory, adherence data) unchanged.
- **FR-007**: The current coaching mode MUST be persisted so that it survives a restart of the application,
  the same way the current plan state is today.
- **FR-008**: In freestyle mode, the system MUST continue to detect newly published activities and generate
  post-activity feedback, without asserting a match or a miss against a planned session.
- **FR-009**: In freestyle mode, the system MUST continue to evaluate and surface training-load and
  recovery guardrail signals exactly as it does in goal mode, since those signals depend on the athlete's
  history rather than on an active plan.
- **FR-010**: Session selection and any figures included in a freestyle-mode proposal (target duration,
  target zone, target load) MUST be produced by deterministic engine logic, never computed by the language
  model, consistent with how session and load figures are produced everywhere else in the system.
- **FR-011**: If the athlete has insufficient fitness history for the system to determine an appropriate
  session in freestyle mode, the system MUST say so explicitly rather than proposing a session based on a
  guessed or default fitness level.
- **FR-012**: Switching coaching mode in either direction MUST NOT delete, alter, or hide any existing
  activity, session log, or coach memory data.
- **FR-013**: When the athlete switches from goal mode to freestyle mode while sessions are still published
  on their calendar for future dates, the system MUST automatically withdraw those future published entries
  (reusing the existing withdrawal capability) as part of the switch, and MUST tell the athlete it did so.

### Key Entities

- **Coaching Mode**: the athlete's current way of using the coach — freestyle (no goal, on-demand
  suggestions) or goal (active plan toward a target date). Exactly one is active at a time, per Principle II
  (single-user, no concurrent multi-plan support).
- **Session Suggestion**: a single on-demand proposal generated in freestyle mode — session type, target
  duration, target zone, and the fitness/preference reasoning behind it — distinct from a `SessionSpec`
  belonging to a periodized plan in that it has no day-of-week, week number, or periodization phase.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete with no active plan gets a concrete, usable session suggestion within a single
  chat exchange (one request, one answer, no follow-up question needed under normal conditions).
- **SC-002**: 100% of activities published while in freestyle mode receive post-activity feedback, matching
  the delivery rate athletes already get in goal mode.
- **SC-003**: 100% of guardrail signals that would fire in goal mode for a given training history also fire
  in freestyle mode for the same history.
- **SC-004**: Switching coaching mode in either direction preserves 100% of pre-existing activities, session
  logs, and coach memory entries, verified by comparing counts before and after the switch.
- **SC-005**: Zero freestyle-mode session suggestions reference a periodization concept (phase, week number)
  that does not apply outside a plan.
- **SC-006**: 100% of future-dated published calendar entries are withdrawn when an athlete switches from
  goal mode to freestyle mode, verified by checking the calendar immediately after the switch.

## Assumptions

- Freestyle mode reuses the existing session library content (`sessions/*.yaml`); this feature changes how a
  session is *selected* in the absence of a plan, not what sessions exist.
- Freestyle mode is reactive only: the athlete asks for a session when they want one, and the coach reacts
  to published activities. It does not add a new proactive daily reminder — the existing session-reminder
  feature stays tied to an active plan's calendar, since freestyle mode has no day-of-week to anchor a
  reminder to.
- Guardrails, response verification, and coach memory continue to operate exactly as they do today; this
  feature only extends where their inputs come from (athlete history in general, not a specific plan), not
  their logic.
- Entering goal mode from freestyle mode reuses the existing goal-setting flow behavior (a plan is generated
  from current fitness, as `/goal` already does), rather than introducing a second, different plan-creation
  path.
- Only one coaching mode is active at a time; this mirrors the existing single active plan assumption
  (Principle II) and is not changed by this feature.
- Freestyle mode is purely on-demand: no new proactive nudge or inactivity reminder is introduced by this
  feature (confirmed). A "haven't trained in a while" nudge, if wanted later, is a separate feature.
- Switching from goal mode to freestyle mode always withdraws future-dated published calendar entries
  automatically (confirmed) — the athlete never ends up with stale calendar entries the coach no longer
  tracks.
