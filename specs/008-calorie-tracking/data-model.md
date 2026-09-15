# Data Model: Daily Calorie Tracking

**Feature**: 008-calorie-tracking | **Date**: 2026-09-15

Entities from [spec.md](./spec.md) §Key Entities, resolved against the codebase's existing model/repository
conventions ([research.md](./research.md) R6).

---

## Persisted

### `MealEntry` — one row per logged meal or day-recap

The only new table. Mirrors `SessionLog`'s shape (`app/db/models/session_log.py`) closely enough to reuse
its conventions directly: `Uuid` PK, `TimestampMixin` for `created_at`/`updated_at`, a cascade-delete
relationship from `User`.

| Column | Type | Rule |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | FK → `users.id`, `ondelete="CASCADE"`, indexed | |
| `entry_date` | `Date`, not null | which day this entry counts toward — **not** necessarily the day it was logged (FR edge case: "hier soir j'ai mangé…", R1/`days_ago`) |
| `entry_type` | `String(16)`, not null | `"meal"` \| `"day_recap"` |
| `meal_slot` | `String(16)`, nullable | `"breakfast"` \| `"lunch"` \| `"dinner"` \| `"snack"` \| `"other"` \| `None`. Always `None` for `entry_type="day_recap"`; may be `None` for `"meal"` too — slots are informal, the athlete is never required to name one (spec Assumptions) |
| `raw_description` | `Text`, not null | the athlete's own words, verbatim — the literal `user_message` that triggered the tool call, not an LLM paraphrase (R1) |
| `estimated_calories` | `Integer`, not null | the LLM's estimate for **this entry only**; always `> 0` (validated before insert, R6 open question 1) |
| `created_at` / `updated_at` | `TimestampMixin` | when the entry was recorded — distinct from `entry_date`, exactly like `SessionLog.logged_date` vs its `created_at` |

**Index**: `idx_meal_entries_user_date` on `(user_id, entry_date)` — every query this feature makes (day
sum, day-recap replace, history range, evening-reminder existence check) filters on exactly this pair.

**`User` relationship**: `meal_entries: Mapped[list["MealEntry"]] = relationship(back_populates="user",
cascade="all, delete-orphan")` — same declaration style as `session_logs`/`chat_messages`/`activities` on
`app/db/models/user.py`.

**Deliberately excluded from `/reset`'s purge** (`app/db/repositories/user_repo.py::_PURGE_MODELS`,
research R5): nutrition history is neither training data nor athlete identity — a third category the
athlete explicitly asked to survive a training reset. The implementation task extends the explanatory
comment already at that line rather than silently leaving a table unmentioned.

---

## Derived, not stored

### Daily calorie total

The spec's "Daily Calorie Total" key entity (spec.md) — **never its own row**. Computed on demand by
`meal_entry_repo.daily_totals(session, user_id, start_date, end_date)`, a single `SELECT ... GROUP BY
entry_date` returning one result per day **that has at least one entry** (FR-008: a day with zero rows is
absent from the result, not present with total `0` — the caller distinguishes "nothing logged" from "logged
and it was zero," which never happens but the shape still matters for correctness). A lightweight
dataclass, not a table:

```python
@dataclass(frozen=True)
class DailyCalorieTotal:
    entry_date: date
    total_calories: int
    entry_count: int
```

This is the same "derive on demand, never cache" rule spec 006's guardrail findings follow (data-model.md
§Derived) and for the same reason: the underlying rows are cheap to sum and a cached total would need its
own invalidation story for zero benefit.

---

## Repository surface — `app/db/repositories/meal_entry_repo.py`

Five functions, mirroring `guardrail_repo.py`'s size and shape (a handful of narrow, explicit functions,
no generic CRUD abstraction):

| Function | Used by |
|---|---|
| `create(session, *, user_id, entry_date, entry_type, meal_slot, raw_description, estimated_calories) -> MealEntry` | `log_meal` tool |
| `delete_for_date(session, user_id, entry_date) -> int` | `log_meal` tool, only when `entry_type == "day_recap"` (FR-009 replace semantics) — returns count deleted so the tool result can report `replaced_existing_entries` |
| `get_latest_for_date(session, user_id, entry_date) -> MealEntry \| None` | `undo_last_meal_entry` tool |
| `delete(session, entry: MealEntry) -> None` | `undo_last_meal_entry` tool |
| `daily_totals(session, user_id, start_date, end_date) -> list[DailyCalorieTotal]` | `get_calorie_history` tool; `log_meal` tool (re-read the day total after insert); the evening reminder's per-user existence check (`daily_totals(..., today, today)` non-empty ⇒ already logged) |

No sixth "does an entry exist" function — `daily_totals` over a single day answers that question without
adding a distinct code path (research R6).

---

## Tool contracts — see [contracts/nutrition-tools.md](./contracts/nutrition-tools.md)

Three additions to `app/llm/tools.py::TOOL_DEFINITIONS`. Full JSON-Schema-shaped parameter tables and
example tool results are in the contract file; not duplicated here.

## What this feature does not model

No macro breakdown, no calorie targets, no correlation with training load (spec Scope). No soft-delete or
edit-in-place for a wrong entry — correction is undo-then-re-log (`undo_last_meal_entry` + a fresh
`log_meal` call), the same "hard delete, no archive" posture spec 007's `/reset` already established for
this codebase, not a new precedent. **Accepted risk, stated plainly**: if a day-recap entry (which already
replaced that day's individual meal entries, FR-009) is itself undone, those replaced entries are not
recoverable — `undo_last_meal_entry` only removes the single most recent row. This is the direct
consequence of FR-009's "replace" choice and FR-010's "hard delete" convention taken together; low-stakes
given the data is an estimate, not a training record, but worth knowing rather than discovering.
