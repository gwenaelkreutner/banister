# Quickstart: Freestyle Coaching Mode

Manual validation scenarios proving the feature end-to-end, once implemented. Mirrors the style of
`specs/005-planned-workout-push/quickstart.md` (script-assisted, real Telegram exchange) — adapt commands to
whatever scripts `/speckit-tasks` creates under `scripts/`.

## Prerequisites

- A connected intervals.icu account with at least a few weeks of activity/wellness history (so
  CTL/ATL/TSB are non-null — see FR-011's edge case for the empty-history path, tested separately below).
- Bot running (`python -m uvicorn app.main:app --port 8000 --reload`) with the poller enabled.
- Migration applied (`alembic upgrade head` picks up the new nullable columns on `session_logs`
  automatically at startup, per `app/db/lifecycle.py::run_migrations()` — no manual step).

## Scenario A — Ask for a session with no plan (US1)

1. Ensure no active plan: if one exists, switch to freestyle first (Scenario C), or use a fresh
   `/setup`-only account that never ran `/goal`.
2. In chat: "Je veux rouler aujourd'hui, tu me proposes quoi ?"
3. **Expect**: one concrete session (type, duration, target zone) referencing current fitness, e.g. "vu ton
   TSB à -6 et ta charge des 7 derniers jours, je te propose 90 min en Z2." No mention of a plan week or
   periodization phase (SC-005).
4. Repeat step 2 immediately after logging a hard effort (Scenario B) and confirm the proposal changes
   accordingly (Acceptance Scenario 3 of US1) — should not repeat a hard session two days running.

## Scenario B — Post-activity feedback with no plan (US3)

1. While in freestyle mode, publish a ride on intervals.icu.
2. Wait for a poller tick (or trigger one manually via whatever test script `/speckit-tasks` provides for
   spec 002's poller).
3. **Expect**: a feedback message arrives, same staged shape as goal mode, with no claim of a matched or
   missed planned session (Acceptance Scenario 1).
4. Check the DB: a `session_logs` row exists for this activity with `plan_id IS NULL` (SC-002, SC-005).

## Scenario C — Switch from goal mode to freestyle (US2, direction 1)

1. Start with an active plan that has at least one session published to the calendar for a future date
   (`/publish`).
2. Run `/goal`, choose "🚴 Pas d'objectif / mode libre."
3. **Expect**: confirmation message stating the plan is deactivated, the calendar entries were withdrawn,
   and what history is kept (FR-006, FR-012, FR-013).
4. Check the athlete's intervals.icu calendar directly: no future `banister:` events remain for that plan
   (SC-006).
5. Check the DB: prior `session_logs`, `activities`, and `athlete_profiles.coach_memory` rows are unchanged
   in count (SC-004).

## Scenario D — Switch from freestyle to goal mode (US2, direction 2)

1. Starting in freestyle mode (no active plan), run `/goal`, choose any of the four existing goal types,
   provide a date.
2. **Expect**: a plan is generated from current fitness (unchanged `_regenerate()` behavior), and the
   confirmation summary omits any "ce qui change vs l'ancien plan" paragraph (there was no old plan) while
   still listing what history is kept.
3. Ask for a session in chat immediately after: **expect** the coach now answers from the plan (upcoming
   sessions), not a freestyle suggestion — confirms tool-list filtering (Research Decision 6) switched over.

## Scenario E — Guardrails still fire in freestyle mode (US3, Acceptance Scenario 2)

1. In freestyle mode, engineer (or wait for) a training-load pattern that would trigger a guardrail in goal
   mode — e.g. a sharp ramp in CTL, per `guardrail_thresholds.py`.
2. Ask the coach anything in chat.
3. **Expect**: the same guardrail finding appears in the response as it would in goal mode (SC-003).

## Scenario F — Insufficient history (edge case)

1. Use a freshly connected account with under a week of wellness history.
2. Ask for a session suggestion in chat.
3. **Expect**: an explicit "not enough data yet" answer (FR-011) — never a guessed session.
