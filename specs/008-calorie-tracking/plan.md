# Implementation Plan: Daily Calorie Tracking

**Branch**: `008-calorie-tracking` | **Date**: 2026-09-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-calorie-tracking/spec.md`

## Summary

Let the athlete describe what they ate — a single meal or a whole day at once — in free-text chat, exactly
the way they already report injuries or ask for their upcoming sessions. The LLM estimates calories from its
own food knowledge (no external nutrition database, per the athlete's explicit choice) and calls a new tool;
the tool persists the entry and returns a **deterministically summed** running day total, which the coach
reports back explicitly labelled as an estimate. A second tool lets the athlete undo a mis-logged entry; a
third retrieves recent history. A daily ~22:00 reminder nudges the athlete if nothing was logged that day.

Phase 0 ([research.md](./research.md)) found the codebase already has the exact mechanism this needs — the
five-tool agentic-loop pattern in `app/llm/tools.py`/`app/llm/chat.py` — so this feature adds to that
pattern rather than building a new one. Two things had to be checked rather than assumed: whether a
calorie-estimate feature sits inside or outside Constitution Principle I's "LLM never computes training
load" (outside — it's a different category of number, R2), and whether the existing response-verification
machinery (spec 006) should be extended to cover it (no — it's training-vocabulary-specific by design, R3).

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: aiogram v3 (no new bot state — the athlete never leaves the chat path),
SQLAlchemy async, Pydantic v2, pytest. No new dependency — no external nutrition API (FR-012, spec Q2).

**Storage**: SQLite via SQLAlchemy async. **One new table, one Alembic revision**: `meal_entries` (see
[data-model.md](./data-model.md)). No changes to existing tables — the evening reminder needs no new column
(research R4).

**Testing**: pytest (`tests/test_db/test_meal_entries.py` for repository/aggregation correctness,
`tests/test_llm/test_nutrition_tools.py` for tool dispatch and validation,
`tests/test_services/test_nutrition_reminder.py` for the reminder's pure selection logic), plus **live
Telegram validation** for the free-text extraction itself (quickstart Scenario 1) — an LLM tool-call
decision isn't something a fixture can fully stand in for.

**Target Platform**: Self-hosted single-process (unchanged)

**Project Type**: Single Python application

**Performance Goals**: Not a factor — one LLM tool call per logged entry, one indexed `SELECT ... GROUP BY`
per history/reminder check.

**Constraints**:
- Free-text in, no rigid command syntax (FR-001, SC-001)
- Every calorie figure shown to the athlete is explicitly labelled an estimate (FR-005, SC-003) — enforced
  in the tool's description text reaching the model, since there's no deterministic ground truth to check it
  against (unlike training claims, which spec 006's verifier checks)
- The day **total**, unlike the per-entry estimate, is fully deterministic arithmetic and must be computed
  in the repository layer, never re-added by the model (Constitution III, research R2)
- A day-recap logged over existing meal entries replaces them, never sums with them (FR-009)
- History and the reminder must both represent "nothing logged" as absent, never as zero (FR-008)
- Fully standalone for this version — no change to `/recap`, `build_system_prompt`'s coaching context, or
  `response_verification.py` (FR-013, research R3)
- Nutrition history is exempt from `/reset`'s purge, by explicit athlete request (spec Assumptions, research
  R5) — the exemption must be visible in code, not just true by omission

**Scale/Scope**: One athlete. Three new LLM tools, one new table, one new repository, one new scheduler
loop. No new bot command, no new FSM state.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

Constitution is **v1.1.0** (amended 2026-09-01).

| Principle | Assessment |
|---|---|
| **I. Deterministic engine, zero LLM load calculation** | **PASS — out of scope by the principle's own wording, checked rather than assumed (research R2).** The principle binds "training load" quantities; a calorie estimate from a food description has no training-load meaning and no deterministic ground truth to compute it from. What *is* arithmetic — summing a day's entries — is kept deterministic in `meal_entry_repo.daily_totals`, consistent with the principle's spirit even where its letter doesn't reach. |
| **II. Single-user, local-first** | **PASS.** No new external service (FR-012 rejects an external nutrition database for this version). No new secret, no new outbound call. |
| **III. Clean layered architecture** | **PASS.** New persistence in `app/db/models/meal_entry.py` + `app/db/repositories/meal_entry_repo.py`, called directly from tool functions in `app/llm/chat.py` — the identical shape every existing tool already uses (`update_injury_status`, `update_coach_memory`, etc. call `repo.*` directly from `_execute_tool`). `app/llm/tools.py` gains three schemas and zero calculation logic. No new top-level package — the scope doesn't earn one (research R6). |
| **IV. Explicit data provenance, never estimate silently** | **PASS, by extension of the principle's spirit rather than its literal scope** (it's framed around measured-vs-computed training values, and there's no competing "measured" calorie source here). The feature's version of this discipline is FR-005/SC-003: an estimate is always presented as one, never smuggled in as if measured. |
| **V. Engine logic is test-covered** | **N/A — no `app/engine/` change.** This feature deliberately stays out of `engine/` (research R6); its deterministic surface (the repository's aggregation) is tested under `tests/test_db/`, mirroring how `weekly_adherence_repo`'s upsert logic is tested there rather than under `test_engine/`. |

### Finding: two things this feature explicitly does NOT do, and why that's the compliant choice

1. **Does not add a `calories`/`kcal` anchor to `response_verification.py`.** Considered and rejected
   (research R3) — that module is training-vocabulary-scoped by construction (Constitution I), and bolting
   on an unrelated domain now, ahead of any actual integration with training advice (FR-013 keeps this
   feature standalone), would be scope creep into a deliberately narrow, already-calibrated module.
2. **Does not add `MealEntry` to `/reset`'s `_PURGE_MODELS`.** The athlete explicitly asked for nutrition
   history to survive a training reset (spec Assumptions); the task list must extend the explanatory comment
   at that tuple, not just leave the table unlisted and unexplained (research R5).

## Project Structure

### Documentation (this feature)

```text
specs/008-calorie-tracking/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/nutrition-tools.md
├── checklists/requirements.md   (pre-existing)
└── tasks.md                     (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/db/models/
├── meal_entry.py                # NEW — MealEntry (Uuid PK, user_id FK cascade, entry_date, entry_type,
│                                 #        meal_slot, raw_description, estimated_calories, TimestampMixin)
└── __init__.py                  # MODIFIED — export MealEntry
└── user.py                      # MODIFIED — meal_entries relationship (cascade="all, delete-orphan")

app/db/repositories/
├── meal_entry_repo.py           # NEW — create / delete_for_date / get_latest_for_date / delete /
│                                 #        daily_totals (returns list[DailyCalorieTotal])
├── __init__.py                  # MODIFIED — export meal_entry_repo
└── user_repo.py                 # MODIFIED — comment only, at `_PURGE_MODELS`: name the new
                                  #            "personal record, not training data" exclusion category

app/llm/
├── tools.py                     # MODIFIED — +3 TOOL_DEFINITIONS: log_meal, undo_last_meal_entry,
│                                 #            get_calorie_history (contracts/nutrition-tools.md)
└── chat.py                      # MODIFIED — tool_executor closure gains raw_message=user_message;
                                  #            _execute_tool dispatches the 3 new tools; three new
                                  #            _tool_* functions call meal_entry_repo directly

app/main.py                      # MODIFIED — _nutrition_reminder_scheduler(bot) (mirrors
                                  #            _weekly_recap_scheduler's fixed-time-sleep shape,
                                  #            research R4), wired into lifespan alongside the
                                  #            existing scheduler tasks (create + cancel on shutdown)

migrations/versions/             # NEW revision — meal_entries table (alembic revision --autogenerate)

scripts/nutrition_state.py       # NEW — --describe : today's entries + running total (Scenario 0),
                                  #        mirrors scripts/guardrail_state.py

tests/
├── test_db/test_meal_entries.py           # NEW — create, delete_for_date replace semantics,
│                                           #        daily_totals aggregation & "absent day" shape,
│                                           #        cascade delete on user removal
├── test_llm/test_nutrition_tools.py       # NEW — tool dispatch, estimated_calories bounds validation,
│                                           #        day_total matches independent repo query,
│                                           #        get_calorie_history's per-day "logged" shape
├── test_services/test_nutrition_reminder.py  # NEW — pure "which users need a reminder today" selection,
│                                              #        decoupled from the asyncio sleep loop
└── test_bot/test_reset.py                 # MODIFIED — assert meal_entries rows survive /reset
```

**Structure Decision**: existing layout, no new top-level package (research R6). The judgement call worth
naming: `meal_entry_repo.daily_totals` — not a generic per-entry `list()` — is the one query every consumer
(the `log_meal` tool, `get_calorie_history`, the reminder) actually needs, so it's written once and reused
rather than each caller summing a raw row list itself; that reuse is also what keeps the "day total is
always deterministic" guarantee (Constitution III) enforceable at one call site instead of three.

## Complexity Tracking

No Constitution Check violations to justify.

## Phase boundaries and known scope limits

Recorded as decisions, so they read as decisions later rather than gaps discovered by accident:

1. **No external nutrition database for this version** (FR-012, research R2). If estimate consistency
   proves to be a real problem in practice (the same phrase estimated differently a week apart), grounding
   against a reference like Open Food Facts is the natural next step — but it's a new external dependency
   with its own text-to-food matching problem, deliberately not taken on now.

2. **Fully standalone from training coaching for this version** (FR-013). No change to `/recap`, to
   `build_system_prompt`'s coaching context, or to `response_verification.py`. The athlete explicitly said
   "on verra pour la suite comment l'intégrer" — this plan makes that integration possible later (the day
   total is already a clean, queryable figure) without doing it now.

3. **Correction is undo-then-relog, not edit-in-place** (FR-010). `undo_last_meal_entry` only ever removes
   today's single most recent row. This mirrors spec 007's `/reset` posture (hard delete, no archive) rather
   than introducing a new editing UX for a P3 story.

4. **The evening reminder is fixed at ~22:00 for every athlete, no `/command` to change it** (spec
   Assumptions — explicitly left as a planning-phase call). If per-athlete configurability is wanted later,
   `app/bot/routers/reminders.py`'s existing pattern (enable/disable + time-slot keyboard) is the template to
   follow — not reinvented here.

5. **The reminder scheduler reuses the existing fixed-UTC+1 "CET" convention**, DST drift and all (research
   R4), rather than introducing a `ZoneInfo`-correct scheduler that would disagree with the session reminder
   already shipping. Fixing that properly is a change to both reminders at once, out of this feature's scope.

6. **`docs/ARCHITECTURE.md` stays as last rewritten** (specs 005/006/007 precedent) — `CLAUDE.md` is the
   living reference the constitution names; this feature's task list updates it (new table in the schema
   table, new tools in the navigation table, the `/reset` exclusion noted).
