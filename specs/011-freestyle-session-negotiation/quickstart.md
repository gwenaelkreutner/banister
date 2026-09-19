# Quickstart: Freestyle Session Negotiation

Manual validation scenarios, once implemented.

## Prerequisites

- Spec 009 (freestyle mode) already deployed and working.
- Athlete in freestyle mode (no active plan), with enough fitness history for the coach to normally
  compute a suggestion.

## Scenario A — Explicit type request overrides the fitness-driven default (US1)

1. Get into a state where the coach would normally suggest recovery/endurance (e.g. right after a hard
   effort, or with a low/negative TSB).
2. Ask in chat: "je veux faire une séance d'intervalles".
3. **Expect**: the suggestion is of type `intervals`, scaled to real current fitness (not a stock value),
   and the narrated text plainly states this isn't what the coach would have proposed unprompted.

## Scenario B — Explicit type request that already matches the default (US1)

1. Get into a state where the coach would normally suggest intervals (fresh, good form).
2. Ask for an interval session explicitly.
3. **Expect**: the suggestion is of type `intervals`, with no conflict note — nothing in the narration
   suggests a disagreement that doesn't exist.

## Scenario C — Unsupported type is declined plainly (US1 Edge Case)

1. Ask for a session type the coach doesn't offer (e.g. "je veux faire du yoga").
2. **Expect**: the coach says it doesn't offer that type — never a session of an unrelated type presented
   as if it were what was asked for.

## Scenario D — One-off style preference changes which session is offered (US2)

1. Ask for a session, note which concrete session was offered.
2. Reply with a style preference for the same type, e.g. "pas celle-là, plutôt une séance steady sans
   répétitions".
3. **Expect**: a different session of the same type is offered, one that already exists in the library —
   never a structure invented for the occasion.

## Scenario E — Duration ceiling is respected (US3)

1. Ask for a session mentioning a time limit, e.g. "j'ai 45 minutes ce soir, propose-moi quelque chose".
2. **Expect**: the returned session's duration does not exceed 45 minutes.

## Scenario F — Duration ceiling too tight for anything to fit (US3 Edge Case)

1. Ask for a session with an unrealistically short ceiling for the resolved type (e.g. 10 minutes for an
   interval session).
2. **Expect**: the coach says plainly that nothing fits within that time — never a session that runs over.

## Scenario G — No preference expressed behaves exactly like today (FR-007 regression check)

1. Ask for a session with a plain request carrying no type/duration/style signal, e.g. "propose-moi une
   séance".
2. **Expect**: the same fitness-driven type choice and rotation-based session pick as before this feature
   shipped — no behavior change.
