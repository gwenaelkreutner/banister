# Contract: The Persistence Boundary

**Feature**: 003-local-embedded-database | **Date**: 2026-08-27

This feature exposes no external interface. Its contract is internal and negative: **the repository layer
is the boundary, and this migration must not move it.** Everything above `app/db/` should be unable to
tell which storage engine is underneath.

That invariant is what makes the change reviewable. A diff that touches a router, an engine module, or a
service is a diff that has escaped its scope.

---

## The surface that must not change

Thirty-three functions across eight repositories. Their names, parameters, return types, and behaviour are
frozen for the duration of this feature.

| Repository | Functions |
|---|---|
| `activity_repo` | `bulk_insert`, `get_for_user`, `clear_for_user` |
| `chat_repo` | `create_message`, `get_conversation`, `clear_conversation` |
| `oauth_repo` | `get_connection`, `upsert_connection`, `delete_connection`, `update_tokens` |
| `plan_repo` | `get_active_plan`, `create`, `update_narrative`, `get_all_active`, `set_start_date` |
| `profile_repo` | `get_by_user_id`, `create`, `update`, `update_injury_status`, `update_coach_memory`, `update_athlete_notes` |
| `session_log_repo` | `create`, `get_by_date`, `get_all_for_user`, `already_logged`, `get_by_strava_activity` |
| `user_repo` | `get_single_user`, `get_by_telegram_id`, `create_single_user`, `get_users_to_remind`, `update_reminder_settings` |
| `weekly_adherence_repo` | `upsert`, `get_recent` |

**Two of these are named for behaviour the target engine expresses differently.** `upsert_connection` and
`weekly_adherence_repo.upsert` are implemented today with a PostgreSQL-specific construct. Their
*implementation* changes; their *signature and semantics* do not. Calling `upsert` twice with the same key
must still produce one row with the second call's values.

---

## Behavioural guarantees the boundary provides

These are what callers already rely on, whether or not they know it. Each is a requirement in the spec and
each is empirically at risk on the target engine.

1. **Deleting a parent removes its children.** Every table cascades from `users`; `session_logs` also
   cascades from `training_plans`. Callers rely on this and never delete children explicitly.
   → FR-006. Verified silently broken by default on the target — see research R1.

2. **A structured document survives a round trip unchanged.** Nesting, value types, and the distinction
   between an absent value and an empty one are preserved. Callers store validated schema objects and
   re-validate on read; a lossy round trip surfaces as a validation error far from its cause.
   → FR-007.

3. **A structured document modified in place is persisted.** The codebase already documents this hazard:
   mutating a document column without flagging it discards the change silently. The port must not
   reintroduce or worsen it.
   → FR-008.

4. **A stored timestamp denotes the same instant when read back.** Callers compute daily boundaries from
   these values for reminders, weekly aggregation, and the ±2-day matching window.
   → FR-010. Verified to lose its offset by default on the target — see research R2.

5. **Unknown is not zero.** `None`, `0`, `False`, and `{}` remain four distinct stored states. Most
   quality metrics on `session_logs` are nullable floats feeding chronic load and adherence.
   → FR-011.

6. **Identifiers stay valid and unique.** Existing UUIDs continue to identify the same rows.
   → FR-012.

7. **Concurrent callers do not lose writes, and contention never reaches the athlete.** Several
   schedulers and the interactive handler share one writer on the target.
   → FR-013, FR-014.

---

## What callers must NOT start doing

The migration creates two temptations, both of which would move the boundary:

- **Handling storage-contention errors at the call site.** If a caller ever needs to catch a
  "database is locked" condition, the connection layer has failed its obligation under FR-014. Fix it
  there, not at the call site.
- **Reaching past the repositories.** An audit for spec 002 already found direct data access inside an
  event handler, which violates the project's own architecture rule. The port must not add a second
  instance, and this contract is the reason to say no.

---

## Verification

The contract is satisfied when:

1. `git diff` for this feature touches only `app/db/`, `app/config.py`, `app/main.py` (lifespan wiring),
   `migrations/`, `scripts/`, `tests/`, and container configuration. **No router, service, engine, or LLM
   module appears in the diff.**
2. The existing 164-test suite passes unchanged against the new engine — unchanged being the point, since
   those tests were written against the old one.
3. New tests assert each of the seven guarantees above directly, including the three that fail silently by
   default rather than raising.
