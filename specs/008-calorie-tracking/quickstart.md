# Quickstart: Validating Daily Calorie Tracking

**Feature**: 008-calorie-tracking | **Date**: 2026-09-15

How to prove this feature works. Each scenario maps to a user story and success criterion in
[spec.md](./spec.md). Per this project's practice (memory: live testing finds real bugs a green suite
misses), Scenario 1 should be run against the real Telegram bot, not just pytest — this is a free-text LLM
extraction path, and the one thing fixtures can't tell you is whether the model actually calls the tool
(and phrases the result as an estimate) on real athlete phrasing.

---

## Scenario 0 — Confirm the schema landed

```bash
uv run python -m alembic upgrade head
uv run python -m scripts.nutrition_state --describe
```

**Expect**: `meal_entries` exists; the script (new, mirrors `scripts/guardrail_state.py --describe`) prints
today's entries (if any) and the current day total, reading through `meal_entry_repo.daily_totals` — the
same call path the LLM tools use, so this script doubles as a manual sanity check independent of Telegram.

## Scenario 1 — Log a meal in natural language and see the estimate (US1, SC-001, SC-003)

In Telegram: describe a real meal, e.g. *"j'ai mangé une omelette de 3 œufs avec du pain et une pomme"*.

**Expect**:

- The coach calls `log_meal` (check logs for the tool name) rather than answering conversationally.
- The reply states an estimated calorie figure **and explicitly calls it an estimate** (FR-005) — not "tu as
  consommé 650 kcal" but something like "~650 kcal, à vue d'œil."
- The reply states the day's running total, not just this entry (US1 acceptance 2 needs a second message
  logged first to check the total updates).
- No extra steps, no form, no command prefix (SC-001) — one message in, the estimate and total in the same
  reply.

```bash
uv run pytest tests/test_llm/test_nutrition_tools.py -v
```

**Expect** (unit level, deterministic side): `log_meal`'s executor rejects `estimated_calories <= 0` and
`> 8000` without touching the database (contracts §1); `day_total_estimated_calories` in the tool result
always matches a direct `meal_entry_repo.daily_totals` query for that day, computed independently in the
test — this is the arithmetic that must stay correct even though the estimate itself can't be checked
(research R2).

## Scenario 2 — Log a whole day at once, and don't double-count (US2, FR-009)

In Telegram, on a day with **no** prior entries: describe an entire day's food in one message. Then, on a
**different** day that already has individual meal entries logged (from Scenario 1), send a day-recap
message.

**Expect**:

- First case: one entry, `entry_type="day_recap"`, one total.
- Second case: the day-recap **replaces** the existing meal entries for that day (`replaced_existing_entries:
  true` in the tool result) and the coach says so — the total after is the recap's number alone, not the
  recap plus the earlier meals summed (FR-009 — this is the double-counting failure mode the spec calls out
  by name; check it doesn't happen).

```bash
uv run pytest tests/test_db/test_meal_entries.py -v -k replace
```

## Scenario 3 — Review history, and "nothing logged" isn't shown as zero (US3, FR-008)

Log entries on at least two non-consecutive days over the last week, leaving at least one day in between
with nothing. In Telegram: ask for the week's calorie history.

**Expect**:

- Each logged day shows its total.
- The untouched day is stated as **not logged**, never as "0 kcal" (FR-008) — read the actual sentence, not
  just the tool result; this is exactly the kind of distinction that's easy to get right in the tool result
  and lose in the model's phrasing.

```bash
uv run pytest tests/test_llm/test_nutrition_tools.py -v -k history
```

**Expect**: `get_calorie_history`'s result includes every day in the requested range, logged or not, with
`"logged": false` (no `total_calories` key) for empty days — contracts §3.

## Scenario 4 — Correct a wrong entry (US4, FR-010)

Log a deliberately wrong entry today (e.g. state the wrong food), then tell the coach it was a mistake.

**Expect**:

- The coach calls `undo_last_meal_entry`; the day's total drops back to what it was before that entry.
- Re-describing the meal correctly logs a fresh entry and the total reflects only the corrected version.
- Trying to undo with nothing logged today returns the "nothing to undo" result and the coach says so rather
  than silently doing nothing (contracts §2).

```bash
uv run pytest tests/test_db/test_meal_entries.py -v -k undo
```

## Scenario 5 — The evening reminder fires only when nothing was logged (US5, FR-014, SC-005)

This one is easiest to validate by reading the code path plus one live check, not a scheduled 24h wait:

```bash
uv run pytest tests/test_services/test_nutrition_reminder.py -v
```

**Expect** (the pure "who needs a reminder" logic, decoupled from the sleep loop):

- A user with zero `meal_entries` rows for today ⇒ included in the reminder list.
- A user with at least one entry today (meal or day-recap) ⇒ excluded, even if logged minutes ago.

**Live check**: temporarily point the scheduler's target time a couple of minutes into the future (or read
`app/main.py::_nutrition_reminder_scheduler`'s next-occurrence computation against the current clock) and
confirm the actual Telegram message arrives on a day with nothing logged, and does not arrive the next time
if something was logged first. This mirrors how the existing session reminder was validated (no pytest
covers the scheduler loop itself in this codebase — confirmed by grep before writing this plan).

## Scenario 6 — `/reset` leaves nutrition history alone (research R5)

```bash
uv run pytest tests/test_bot/test_reset.py -v
```

**Expect**: the existing reset test suite still passes unchanged, **and** a new assertion confirms
`meal_entries` rows for the user survive a `/reset` — `MealEntry` must not appear in
`user_repo._PURGE_MODELS`. Confirm by reading the tuple, not just by the test passing (the same "grep, not
feeling" standard spec 005 set for its consent barrier).

---

## Definition of done

- [ ] Scenario 0 — migration applied, `scripts/nutrition_state.py --describe` reads real rows
- [ ] Scenario 1 — a real free-text meal produces an estimate that is explicitly labelled as such, plus an
      updated running total, in one exchange
- [ ] Scenario 2 — day-recap replaces same-day meal entries; no double-counted total
- [ ] Scenario 3 — history distinguishes "not logged" from "logged as zero" (which never happens, but the
      shape is checked)
- [ ] Scenario 4 — undo removes exactly the last entry and the total is recomputed correctly
- [ ] Scenario 5 — reminder logic tested directly; one live Telegram confirmation that it actually fires and
      actually skips a logged day
- [ ] Scenario 6 — `/reset` provably does not touch `meal_entries`
- [ ] `ruff check app/ tests/` and the full relevant pytest subset pass
- [ ] `CLAUDE.md` updated: new table in the schema table, new tools in the navigation table, the `/reset`
      exclusion noted where the other identity-survives-reset exceptions already are
