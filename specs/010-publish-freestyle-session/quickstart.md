# Quickstart: Publish a Freestyle Session

Manual validation scenarios, once implemented.

## Prerequisites

- Spec 009 (freestyle mode) already deployed and working.
- A connected intervals.icu account, athlete in freestyle mode (no active plan).
- Bot running with the migration for `freestyle_published_entries` applied
  (`app/db/lifecycle.py::run_migrations()` at startup — no manual step).

## Scenario A — Ask, like it, publish (US1)

1. Ask for a session in chat.
2. **Expect**: a narrated suggestion with a "📅 Publier sur intervals.icu" button attached.
3. Tap the button.
4. **Expect**: the message updates to confirm publication; the athlete's intervals.icu calendar shows a new
   structured workout on the expected date matching what was proposed.
5. Check the DB: a `freestyle_published_entries` row exists with a `content_hash` matching what was
   rendered.

## Scenario B — Negotiate before confirming (US2)

1. Ask for a session; don't like it — ask for a different one.
2. **Expect**: a new suggestion with its own button; the first message's button is still visibly there
   (Telegram doesn't remove it) but is now stale.
3. Tap the **first** (stale) message's button.
4. **Expect**: a "no longer current" response, nothing published.
5. Tap the **second** (current) message's button.
6. **Expect**: that session — not the first one — is what appears on the calendar.

## Scenario C — Withdraw after publishing (US3)

1. Publish a freestyle session (Scenario A).
2. Ask to remove it (exact trigger per whatever `/speckit-plan` wires — e.g. a chat request or a
   command).
3. **Expect**: it disappears from the calendar; other entries (plan sessions, manually created ones) are
   untouched.

## Scenario D — Mode switch withdraws pending freestyle publications (FR-011)

1. Publish a freestyle session for a future date (Scenario A).
2. Switch to goal mode (`/goal`, pick any objective).
3. **Expect**: the freestyle-published entry is withdrawn as part of the switch, same as spec 009 already
   does for plan entries in the other direction.

## Scenario E — Failure is never silent (US1 Acceptance Scenario 3)

1. Temporarily break the intervals.icu connection (e.g. an invalid API key in a test environment) and
   attempt to publish.
2. **Expect**: an explicit failure message — never a confirmation that implies success.

## Scenario F — Double-tap does not duplicate (FR-010)

1. Publish a freestyle session.
2. If the confirmation message's button is still tappable, tap it again (or replay the same callback).
3. **Expect**: no second calendar entry is created.
