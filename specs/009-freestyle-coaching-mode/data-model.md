# Phase 1 Data Model: Freestyle Coaching Mode

## Coaching Mode (derived, not a stored entity)

Not a new table or column (Research Decision 1). Computed at read time:

```
coaching_mode(user) = "goal" if plan_repo.get_active_plan(session, user.id) is not None else "freestyle"
```

Single-user, so exactly one value at a time — no separate storage, no state machine beyond the existing
`training_plans.is_active` flag that already drives it.

## `SessionLog` — relaxed constraints (schema change)

| Column | Today | Change | Why |
|---|---|---|---|
| `plan_id` | `nullable=False`, FK → `training_plans.id`, `ondelete="CASCADE"` | → `nullable=True` | A freestyle-mode logged activity has no plan to belong to (Research Decision 3) |
| `week_number` | `nullable=False` | → `nullable=True` | No periodization week exists outside a plan |
| `day_of_week` | `nullable=False` | → `nullable=True` | No planned day-of-week slot exists outside a plan |
| `status` | `"done" \| "skipped" \| "unplanned"` | unchanged (reuse `"unplanned"` semantics: not matched to any planned session — true by construction when `plan_id IS NULL`) | No new status value needed; a freestyle log is exactly "unplanned" today's enum already models, just also missing a `plan_id` |

**Migration**: one Alembic revision (`alembic revision --autogenerate`, per Development Workflow), relaxing
the three columns above. `ON DELETE CASCADE` on `plan_id` is unaffected — a `NULL` foreign key is simply not
subject to cascade.

**Not changed**: every other `SessionLog` column (quality metrics, `source_activity_id`, `tss_actual`, etc.)
— a freestyle log carries exactly the same activity-quality data a plan-matched log does; only the
plan-position columns become optional.

## `ActivityFeedbackContext.Outcome` — one new value

Today (`app/services/activity_feedback.py:41`): `Literal["matched", "no_plan", "unplanned", "bonus"]`.

Add `"freestyle"`: an activity logged with `plan_id=NULL` because the athlete is in freestyle mode — as
opposed to `"unplanned"`, which means a plan *exists* but this particular activity didn't match any of its
sessions. Keeping them distinct matters for FR-008: a freestyle-mode notification must never claim a match
was attempted and missed (US3, Acceptance Scenario 1) — `"unplanned"`'s existing copy talks about missed
planned sessions, which would be misleading with no plan at all. `"no_plan"` itself is retired — it
described exactly the case this feature now handles, not a case that should still silently drop data.

## `Session Suggestion` (ephemeral — not a persisted entity)

The single on-demand proposal freestyle mode generates when asked. Not stored in the database; recomputed
fresh on every request from current data, the same way a `/forme` reading is never cached.

| Field | Type | Source |
|---|---|---|
| `workout_type` | str (`"recovery"\|"endurance"\|"tempo"\|"intervals"\|"long_ride"`, per existing vocabulary) | Research Decision 4's new selector, from fitness state + recent load |
| `template_id` | str | `session_library.load_library()`, filtered by `workout_type` only (no `phase`) |
| `steps` | `list[Step \| RepeatGroup]` | `fitting.fit_template()` output — existing `Step`/`RepeatGroup` schemas, no new shape |
| `duration_minutes` | int | `fitting.FitResult.duration_minutes` |
| `target_tss` | float | `fitting.FitResult.tss_target` |
| `zone_code` | str | `fitting.FitResult.zone_code` |
| `reasoning_summary` | str | Selector's stated basis (e.g. "TSB -18, charge des 7 derniers jours modérée → séance d'endurance") — always present, never omitted, since a proposal with no visible reasoning is unauditable (Principle IV in spirit: the athlete can see why, not just what) |
| `insufficient_data` | bool + reason str, when true | Set when fitness history doesn't clear the same bar `recovery_insufficiency()` already uses for guardrails (FR-011) — no session fields populated in that case |

This reuses the existing `Step`/`RepeatGroup` Pydantic models from `app/engine/schemas.py` verbatim — a
freestyle suggestion is structurally identical to a `SessionSpec`'s `steps`, just not attached to a
`day_of_week`/`week_number`/plan.

## State transitions

```
[No account connected] --/setup--> [Freestyle mode: no active plan]
                                        |
                                        | /goal (event/fitness/performance/other)
                                        v
                                  [Goal mode: active plan]
                                        |
                                        | /goal → "pas d'objectif"
                                        v
                                  [Freestyle mode: no active plan]
                                  (future published calendar entries withdrawn, FR-013;
                                   activities/session_logs/coach_memory untouched, FR-012)
```

No intermediate/transient state — the switch is a single atomic operation from the athlete's perspective
(one command exchange), backed by two DB writes (`deactivate_all_for_user` + calendar withdrawal) that
either both happen or the athlete is told the switch didn't complete.
