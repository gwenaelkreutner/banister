# Contract: mode switch via `/goal`

Extends the existing `/goal` command (`app/bot/routers/goal.py`) rather than adding a new command
(Research Decision 2).

## Entry point

`/goal` — unchanged trigger, unchanged precondition that onboarding is complete
(`user.onboarding_completed`). The current hard block `if plan is None: "Aucun plan actif — lance /setup."`
(`goal.py:44`) is removed: `/goal` must now work identically whether or not a plan currently exists.

## Keyboard (`_goal_kb()`)

Today:
```
🎯 Événement cible   → goal:type:event
💚 Forme générale    → goal:type:fitness
⚡ Performance        → goal:type:performance
🔹 Autre              → goal:type:other
```

Add a fifth row:
```
🚴 Pas d'objectif / mode libre → goal:type:freestyle
```

## Branch: entering freestyle mode (`goal:type:freestyle`)

Skips `GoalStates.DATE` entirely (no date to ask). Immediately:

1. If a plan is currently active: `plan_repo.deactivate_all_for_user(session, user.id)` (same call
   `_regenerate()` already makes at `goal.py:122`).
2. Withdraw future-dated published calendar entries for that plan — reuse the existing withdrawal
   capability behind `/unpublish` (`withdraw_all_publications()`), scoped to the plan just deactivated. Per
   FR-013, this happens automatically and is reported to the athlete; it is not a separate confirmation
   step.
3. No new `TrainingPlan` row is created — `coaching_mode(user)` (data-model.md) now reads "freestyle" as
   soon as step 1 commits.
4. Reply summarizing what changed and what's kept, in the same voice as `_regenerate()`'s existing summary
   message (`goal.py:147-158`): sessions/activities/coach memory untouched (FR-012), calendar entries
   withdrawn (FR-013), and how to get a suggestion right now (`get_freestyle_session_suggestion` reachable
   simply by asking in chat — no separate command).

If there was no active plan already (already in freestyle mode), this branch is a no-op reply ("Déjà en
mode libre.") rather than an error — idempotent, matching the project's general pattern of treating a
repeated action as a no-op rather than a failure (e.g. `import_history`'s idempotent resume).

## Branch: entering goal mode (existing `goal:type:event/fitness/performance/other`)

Unchanged behavior (`goal.py:58-161`) once the `plan is None` guard at line 44 is removed. When there was no
old plan, `_regenerate()`'s "ce qui change" paragraph (which currently diffs against `old_schema`) must
special-case the from-freestyle case: no old plan to diff against, so that paragraph is omitted and only
"ce qui est gardé" (history) is shown — the same information gap the reused function already handles for a
first-ever `/setup`-driven plan, just reached from a different entry point.

## Non-goals

- No confirmation step before switching to freestyle (Question 1/2 already resolved this: automatic
  withdrawal, no extra prompt) — switching is a single command-and-choice interaction, matching `/goal`'s
  existing single-exchange feel for the other four options.
- No changes to `/reset` or `/setup` — both are unaffected; `/reset` already purges regardless of mode
  (`_PURGE_MODELS` doesn't distinguish), and `/setup` only runs pre-onboarding.
