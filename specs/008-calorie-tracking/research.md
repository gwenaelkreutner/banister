# Research: Daily Calorie Tracking

**Feature**: 008-calorie-tracking | **Date**: 2026-09-15

Phase 0 output. Every decision below was checked against the running codebase, not assumed — the same
"code is authority" discipline the rest of this project follows.

---

## R1 — Logging happens through the existing LLM tool mechanism, not a new command

**Question**: FR-001 requires no rigid command syntax. Does the codebase already have a mechanism for
"free text in, structured record out"?

**Finding**: yes, exactly this shape, five times over. `app/llm/tools.py::TOOL_DEFINITIONS` holds five
OpenAI-format tool schemas (`get_upcoming_sessions`, `update_injury_status`, `propose_plan_modification`,
`propose_session_adjustment`, `update_coach_memory`); `app/llm/chat.py::_execute_tool` dispatches each to a
deterministic Python function. The LLM reads free text, decides which tool applies, extracts structured
arguments, and the tool function does the actual persistence/arithmetic — the LLM never writes to the
database directly. `update_coach_memory` is the closest precedent: the LLM decides *what* to remember
(content is inherently a judgment call, there's no "correct" note the way there's a correct CTL), and the
tool just persists what it's given.

**Decision**: add three tools to `TOOL_DEFINITIONS` — `log_meal`, `undo_last_meal_entry`,
`get_calorie_history` — following the exact same shape. No new bot command, no new FSM state. This is also
why the evening reminder (US5) doesn't need its own command either: it is push-only, athlete replies through
the same chat path.

**Gap found while checking this**: `chat.py`'s `tool_executor` closure (`run_chat`, around line 201) closes
over `user, session, plan, profile, logs, activities` — **not** the raw `user_message`. Every existing tool
works fine without it because none of them need to store the athlete's literal words. `log_meal` does: FR-006
requires the raw description survive, and re-deriving it from the LLM's own paraphrase risks losing exactly
the detail ("3 œufs" vs "des œufs") that made the estimate what it was. `raw_message=user_message` must be
added to the closure and threaded into `_execute_tool`'s signature.

## R2 — Where the estimate comes from, and what "deterministic" still means here

**Question**: Constitution Principle I is unconditional about training load — "the LLM MUST NEVER compute
training load." Calories aren't training load, but the instinct to check is correct: what part of this
feature must still be deterministic?

**Finding**: Principle I's own text scopes itself to "TSS/HRSS, power and heart-rate zones, ATL/CTL/TSB,
periodization, plan structure, adherence KPI, guardrail signals" — training-domain quantities computed from
data the system already holds. A calorie estimate from a free-text food description is a different kind of
number: there is no ground truth in this system to compute it *from*. The LLM's food knowledge **is** the
estimation method (this is FR-012's resolution — ADR below).

What *is* still arithmetic on values already known, and therefore must stay deterministic per Principle
III ("`llm/` MUST NOT perform calculations, only read pre-computed context and generate text"): **summing a
day's entries into a total.** Once `estimated_calories` exists as a stored number, adding several of them
together is exactly the kind of calculation that belongs in `db/repositories/`, not in a prompt. The tool
function computes the day total via a repository query after insert and returns it in the tool result — the
LLM reports a number it was handed, the same pattern `get_upcoming_sessions` already uses for TSS targets.

**Decision (ADR)**: calorie estimation = LLM knowledge, unverified against any reference (FR-012, user
choice). Day-total aggregation = deterministic SQL `SUM`, in the repository layer. This is the same split
Principle I already draws elsewhere: the model narrates and judges qualitative things (what a meal probably
contains), the codebase computes anything that's pure arithmetic on stored data.

## R3 — Response verification (spec 006) is training-vocabulary-anchored; extending it here was considered and rejected

**Question**: `app/services/response_verification.py` already checks that any number the coach states about
the athlete matches what was actually retrieved, and withholds the sentence if not (spec 006 US3). Should a
stated day-total calorie figure go through the same check?

**Finding**: `_ANCHORS` in `response_verification.py` is a **fixed, training-specific keyword list** — `ctl`,
`atl`, `tsb`, `ftp`, `acwr`, `monotony`, `hrv`, `rhr`, `ramp_rate` — each paired with a French translation in
`_METRIC_FR` and a withholding phrase whose wording ("je préfère ne pas avancer de chiffre sur *ta forme de
fond*") is written for training metrics specifically. Retrofitting "calories"/"kcal" as a tenth anchor is
mechanically easy (one more tuple in `_ANCHORS`, one more `_METRIC_FR` entry) but would mean this well-tested,
narrowly-scoped module — built and calibrated for one purpose (spec 006 research R5's false-positive concern
was specifically about training numbers) — starts carrying a second, unrelated domain.

**Decision**: do not extend `response_verification.py` in this feature. It stays exactly what Constitution
Principle I describes it as: verification of *training* claims. The day-total figure is still reliable —
it's computed once in the repository and handed to the LLM in the tool result (R2) — it just isn't run
through the anchored verifier. If FR-013's "standalone for now" is revisited later and nutrition intake
starts appearing in `/recap` or guardrail-adjacent coaching advice, extending the verifier is the natural
next step; doing it now, for a feature whose own spec says "standalone," would be integrating ahead of the
need.

## R4 — The evening reminder needs no new column at all

**Question**: The existing session reminder (`app/bot/routers/reminders.py`, `users.reminder_hour` /
`reminder_minute` / `reminders_enabled` / `reminder_last_sent_at`) is athlete-configurable with idempotency
tracked by a "last sent" date column. Does the nutrition reminder need the same machinery?

**Finding**: the session reminder needs `reminder_last_sent_at` because whether to send is a fact about the
*reminder* (did we already send today's?), independent of any other table. The nutrition reminder's
send/don't-send condition is a fact about **data that already exists**: did the athlete log anything today?
`meal_entries` having zero rows for today *is* the "not yet done" state — recording a second, redundant
"did we remind" flag would be two sources of truth for one question, and could drift (e.g. a crash between
sending and recording). `_weekly_recap_scheduler` already shows the simpler pattern for a fixed-time,
no-per-user-config broadcast: compute the next occurrence, `asyncio.sleep` until then, do the work, loop.

**Decision**: `_nutrition_reminder_scheduler(bot)` mirrors `_weekly_recap_scheduler`'s shape (sleep until
next 22:00, not a per-minute poll like the session reminder) but reuses the athlete-selection query style
of `_run_weekly_recap_broadcast` (`User.is_active == True`). No new column, no new `/command`, no
enable/disable toggle — matches the spec's Assumption that a fixed time is acceptable for v1. **Timezone
note**: the session reminder computes "CET" as a hardcoded UTC+1 offset (`app/main.py`, `_run_session_reminders`),
not a real `Europe/Paris` tzinfo, so it silently drifts an hour off local time during CEST (late March–late
October). This feature reuses the same fixed UTC+1 convention rather than introducing a more correct
`ZoneInfo("Europe/Paris")` scheduler that would behave inconsistently with the reminder mechanism already
shipping — a real fix belongs to both reminders at once, not smuggled into this one.

## R5 — `/reset` must not silently start purging nutrition history

**Question**: spec 007's `/reset` (`app/db/repositories/user_repo.py::purge_athlete_data`) deletes every
per-athlete row across ten models (`_PURGE_MODELS`, line 101). Should `MealEntry` join that tuple?

**Finding**: the spec's own Assumptions (written after the clarification round) are explicit — the athlete
wants calorie history "kept indefinitely... as a base for possible future analysis," independent of the
training relationship `/reset` starts over. `_PURGE_MODELS` already has a comment (line 99) explaining why
`coach_voice`/`disclaimer_acknowledged_at` are deliberately absent ("identity, not training data"). Nutrition
history is a third category the comment doesn't yet name: not training data, not identity — a separate
personal record the athlete explicitly asked to keep across a training reset.

**Decision**: `MealEntry` is **not** added to `_PURGE_MODELS`. The implementation task must extend the
existing comment at that line to name the new category explicitly, so a future reader sees a decision, not
an omission. No behavioural change to `/reset` beyond that comment.

## R6 — No new top-level package; this stays inside the existing layers

**Question**: `app/engine/` is reserved by Constitution Principle I for deterministic training-load logic.
Nutrition isn't training load — does it need its own top-level package (`app/nutrition/`), mirroring
`app/providers/`?

**Finding**: the feature's entire deterministic surface is: one table, five small repository functions
(create / delete-for-date / get-latest-for-date / delete / daily-totals), and three tool functions that call
them. There's no multi-module pipeline (no fetch→analyze→match, no periodization, nothing an `engine/` or a
`providers/`-style boundary earns its keep over). Introducing a new top-level package for this would be the
kind of premature structure the project has otherwise avoided (`app/engine/fitting.py` exists and is
deliberately *not yet wired* rather than forced in before it's needed).

**Decision**: `app/db/models/meal_entry.py` + `app/db/repositories/meal_entry_repo.py`, called directly from
the tool functions in `app/llm/chat.py`, same as every existing tool. No new package.

---

## Open questions carried into implementation

1. **`estimated_calories` sanity bounds.** The plan uses `0 < calories <= 8000` as an implausibility guard
   (rejects `0`, negative, or absurd values a malformed tool call might produce) — not a claim that 8000 is a
   meaningful nutritional ceiling, just a defensive bound. Confirm this doesn't need to be a named constant
   in a thresholds module the way guardrails are (spec 006 precedent) — leaning no, this is a single call-site
   guard, not a documented coaching threshold.
2. **`days_ago` upper bound of 2** (today / yesterday / day-before) is a judgment call matching the spec's
   "hier soir j'ai mangé..." edge case; nothing in the spec asks for logging further back than that, and a
   wider window makes accidental mis-dating (LLM misreads "la semaine dernière" as a specific day) more
   likely rather than less.
