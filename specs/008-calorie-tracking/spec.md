# Feature Specification: Daily Calorie Tracking

**Feature Branch**: `008-calorie-tracking`

**Created**: 2026-09-15

**Status**: Draft

**Input**: User description: Daily calorie tracking from free-text meal descriptions. The athlete tells the
coach, in natural language via the Telegram chat, what they ate — at lunch, at dinner, or as a recap of the
whole day. The system estimates the calories consumed for that entry and accumulates a running daily total,
tracked over time so the athlete can see their caloric consumption day by day. This is a new
nutrition-tracking capability alongside the existing training-coaching features; estimates should be
presented as estimates, not as precise measurements.

## Scope

**In scope**: describing a meal or a whole day's food in natural language via chat, receiving a calorie
estimate for that entry, accumulating a running daily total, and viewing that total (today and past days).

**Out of scope**: macronutrient breakdown (protein/carbs/fat), calorie *targets* or goals, diet coaching or
recommendations, correlating intake with training load in the coach's advice. These may follow in a later
feature once basic tracking is in daily use.

**Depends on**: the existing chat/LLM pipeline (`app/llm/`) for turning a free-text description into an
estimate; the existing per-athlete local storage (SQLite) for history.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Log what I ate and see the estimate (Priority: P1)

The athlete tells the coach, in their own words, what they had for a meal ("j'ai mangé une omelette de 3
œufs avec du pain et une pomme"). The coach replies with an estimated calorie count for that entry and the
new running total for the day.

**Why this priority**: this is the core loop the whole feature exists for — without it there is nothing to
build on.

**Independent Test**: send one free-text meal description in chat; verify a calorie estimate is returned
and a day total appears, without any other part of the feature existing yet.

**Acceptance Scenarios**:

1. **Given** the athlete has logged nothing today, **When** they describe a meal in chat, **Then** the coach
   returns an estimated calorie count for that meal and states it is an estimate.
2. **Given** the athlete has already logged one meal today, **When** they describe a second meal, **Then**
   the coach returns that meal's estimate plus the updated running total for the day.

---

### User Story 2 - Log a whole day at once (Priority: P2)

Instead of logging meal by meal, the athlete describes everything they ate that day in one message (e.g. at
the end of the evening). The coach estimates a total for the day from that single description.

**Why this priority**: many athletes won't log in real time; a single end-of-day recap is the realistic
usage pattern for a lot of users and must be supported, but the per-meal flow (US1) is the more fundamental
building block.

**Independent Test**: send one message describing an entire day's food; verify a single day total is
produced without needing separate per-meal messages.

**Acceptance Scenarios**:

1. **Given** the athlete has logged nothing today, **When** they describe everything eaten that day in one
   message, **Then** the coach returns one estimated total for the day.
2. **Given** the athlete already logged individual meals today, **When** they also send a whole-day recap,
   **Then** the system does not silently double-count — see FR-009.

---

### User Story 3 - Review calorie history (Priority: P2)

The athlete asks to see how many calories they consumed on a given day, or over recent days, to get a sense
of their pattern over time.

**Why this priority**: a running total that can never be reviewed again provides little standalone value;
seeing the history is what makes the tracking useful.

**Independent Test**: after logging entries across more than one day, request the history; verify each
day's total is shown correctly attributed to its date.

**Acceptance Scenarios**:

1. **Given** the athlete logged food on several different days, **When** they ask for their recent calorie
   history, **Then** the coach shows a per-day total for each of those days.
2. **Given** the athlete has not logged anything on a given day, **When** they ask about that day, **Then**
   the coach states nothing was logged rather than showing zero as if it were a measured value.

---

### User Story 4 - Correct a logged entry (Priority: P3)

The athlete realizes an entry was wrong (wrong food, wrong quantity, or logged for the wrong meal) and wants
to fix or remove it without contacting support or editing a database by hand.

**Why this priority**: estimates and free-text descriptions will sometimes be wrong or mistyped; this is a
quality-of-life safeguard, not core to the first usable version.

**Independent Test**: log an entry, then correct or delete it; verify the day's running total updates to
reflect the correction.

**Acceptance Scenarios**:

1. **Given** the athlete logged an entry today, **When** they ask to remove or correct it, **Then** the
   entry is removed or replaced and the day's total is recalculated.

---

### User Story 5 - Evening reminder if nothing was logged (Priority: P2)

If the athlete hasn't logged anything by the evening (around 22:00), the coach sends a reminder message so
tracking doesn't silently lapse on busy days.

**Why this priority**: tracking that depends entirely on the athlete remembering to open the chat will decay
in practice; a proactive nudge is what makes daily tracking sustainable, mirroring the existing training
session reminder (`app/bot/routers/reminders.py`).

**Independent Test**: with no entries logged for the current day, wait for (or simulate) the reminder time;
verify a reminder message is sent. With at least one entry already logged that day, verify no reminder is
sent.

**Acceptance Scenarios**:

1. **Given** the athlete has logged no calorie entry for today, **When** the evening reminder time is
   reached, **Then** the coach sends a message inviting them to log what they ate.
2. **Given** the athlete has already logged at least one entry today (meal or day-recap), **When** the
   reminder time is reached, **Then** no reminder is sent.

---

### Edge Cases

- The athlete describes food with no usable quantity ("j'ai mangé des pâtes") — the system must still
  produce an estimate rather than refusing, while communicating that the estimate is rough (FR-005).
- The athlete sends something that is not a food description at all (e.g. asks a training question in the
  same breath, or sends gibberish) — the system must not fabricate a calorie estimate for non-food input.
- The athlete logs food for a date other than today (e.g. "hier soir j'ai mangé...").
- The athlete logs the same meal twice by mistake.
- The estimation step (LLM call) fails or times out — the athlete must be told the entry wasn't recorded,
  never shown a silent zero or a guessed number presented as if it succeeded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The athlete MUST be able to describe a meal (lunch, dinner, snack, or any single eating
  occasion) in free-text natural language via the existing chat, without a separate rigid command syntax.
- **FR-002**: The athlete MUST be able to describe an entire day's food in a single free-text message and
  receive one estimate covering that whole description.
- **FR-003**: For each logged entry, the system MUST produce an estimated calorie count.
- **FR-004**: The system MUST maintain a running total of estimated calories per calendar day, derived from
  that day's logged entries.
- **FR-005**: Every calorie figure shown to the athlete (per entry or per day) MUST be presented explicitly
  as an estimate, never as a precise or verified measurement.
- **FR-006**: The system MUST persist logged entries (raw description, estimated calories, the date and,
  where known, the meal/slot they belong to) so history survives across sessions and app restarts.
- **FR-007**: The athlete MUST be able to retrieve the total for a specific past day and for a recent range
  of days.
- **FR-008**: If a day has no logged entries, the system MUST represent that as "nothing logged," distinct
  from a day where the athlete ate nothing.
- **FR-009**: When a whole-day recap (US2) is logged on a day that already has individual meal entries, the
  system MUST replace those existing entries with the day recap (superseding, not summing), and MUST tell
  the athlete that the day's prior entries were replaced.
- **FR-010**: The athlete MUST be able to correct or remove a previously logged entry, with the affected
  day's total recalculated accordingly.
- **FR-011**: If the system cannot produce an estimate for a given input (e.g. the input is not a food
  description, or the estimation step fails), it MUST tell the athlete the entry was not recorded rather
  than recording a fabricated or zero value.
- **FR-012**: Calorie estimation MUST rely on the LLM's own general food knowledge; no external nutrition
  reference database is required for this version. Estimation quality/consistency may be revisited in a
  later feature if this proves insufficient in practice.
- **FR-013**: This feature's data and behavior MUST remain fully standalone for this version: its own
  command(s) and its own history view, with no required change to `/recap` or the chat's training-context
  system prompt. Surfacing calorie intake alongside training load in the coach's advice is explicitly
  deferred to a later feature.
- **FR-014**: If the athlete has no calorie entry logged for the current day by a fixed evening time
  (default ~22:00, athlete's local time), the system MUST send a reminder message inviting them to log what
  they ate that day. No reminder is sent on a day that already has at least one entry.

### Key Entities *(include if feature involves data)*

- **Meal Entry**: one logged eating occasion or day-recap — free-text description as given by the athlete,
  which day it belongs to, which slot it represents (lunch / dinner / day-recap / other) if determinable,
  the estimated calorie count, and when it was logged.
- **Daily Calorie Total**: the sum of that day's Meal Entries' estimated calories — a derived view over
  entries for a given day, not an independently editable value.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An athlete can go from describing a meal to seeing its calorie estimate and the day's running
  total in a single chat exchange, with no extra steps or forms.
- **SC-002**: An athlete can recover the caloric total for any specific day within the last 30 days with a
  single request.
- **SC-003**: 100% of calorie figures shown to the athlete are visibly marked as estimates.
- **SC-004**: An athlete can correct a wrongly logged entry without needing to re-describe or re-derive the
  rest of that day's entries.
- **SC-005**: An athlete who has not logged anything by the evening is proactively reminded, without needing
  to remember to check on their own.

## Assumptions

- Tracking is for the single owning athlete only, consistent with the project's single-user, local-first
  design (Constitution Principle II) — no multi-user or shared-household tracking.
- "Meal" slots are informal (lunch / dinner / snack / whole day) rather than a fixed enumerated schedule;
  the athlete is not required to tag which meal an entry belongs to.
- Estimates are approximate by nature; no claim of clinical or dietetic accuracy is made or required.
- Logged calorie entries and daily totals are retained indefinitely with no automatic purge — the athlete
  explicitly wants the full history kept, both as a record and as a base for possible future analysis (e.g.
  trends, correlation with training). Mirrors the "kept indefinitely" retention already used for
  `response_check_failures`.
- This feature does not change how training plans, guardrails, or load calculations work; it is additive
  and, per FR-013, stays standalone for this version — integrating it with training context (e.g. `/recap`)
  is left for a future feature.
- The evening reminder reuses the existing session-reminder pattern (`app/bot/routers/reminders.py`,
  `_session_reminder_scheduler` in `app/main.py`) as prior art for a scheduled per-user check, rather than
  introducing a new scheduling mechanism. Whether the reminder time is configurable per athlete (like
  session reminders) or fixed at ~22:00 for v1 is a planning-phase decision, not a scope question.
