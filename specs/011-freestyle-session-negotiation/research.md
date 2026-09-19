# Phase 0 Research: Freestyle Session Negotiation

## Decision 1: One tool call, not a "list candidates then pick" round trip

**Decision**: `get_freestyle_session_suggestion` stays a single call. The full set of session templates
(id + `purpose`/`intent`/`suits`, grouped by `workout_type`) is embedded directly in the tool's parameter
schema as a closed enum + description for `template_id`, so the model can pick a matching template in the
same call where it also states `requested_workout_type`/`max_duration_minutes` — no separate "here are your
options, now choose" tool exchange.

**Rationale**: every other tool in `app/llm/tools.py` resolves in one call; a two-step negotiation would be
the first tool in this codebase to need a second round trip for a single athlete request, doubling latency
and tool-iteration budget (`run_agentic_loop(max_iterations=3)`, shared with any other tool the same turn
might need) for a session library small enough (a handful of templates per type) to just enumerate upfront.

**Alternatives considered**: a first call returning `{"candidates": [...]}` and a second, follow-up call
carrying the chosen id. Rejected — no other tool in this codebase works this way, it would need a new
"pending selection" concept mirroring spec 010's `pending_proposal` for something that doesn't need
confirmation (picking a template isn't a write, unlike publishing one), and it does not actually buy
anything a closed enum in one schema doesn't already provide.

## Decision 2: The template enum is built once, at import time, from `session_library.load_library()`

**Decision**: `app/llm/tools.py` calls `load_library()` (already `@cache`d) when `TOOL_DEFINITIONS` is
built, and derives the `template_id` enum + its per-id description text from it directly. This mirrors
`load_library()`'s own existing convention — cached, "restart the app to pick up `sessions/*.yaml` edits" —
so no new invalidation logic is introduced.

**Rationale**: `session_library.py`'s docstring already states this tradeoff for every other consumer
(`plan_builder.py`, `freestyle_selector.py`); the tool schema should not behave differently from the engine
it's describing. Building it per-request (e.g. inside `tools_for_mode()`, which chat.py already calls fresh
every turn) would add avoidable per-turn work — `load_library()` and `TOOL_DEFINITIONS` are both already
module-level constants, and nothing about template metadata changes within a running process.

**Alternatives considered**: rebuilding the enum inside `tools_for_mode()` on every call, matching how that
function is already invoked per-turn. Rejected as unnecessary work for data that cannot change without a
restart anyway.

## Decision 3: An explicit `requested_workout_type` bypasses the avoid-list outright, not just re-ranks it

**Decision**: `choose_workout_type()` gains `requested_workout_type: str | None = None`. When present and
one of the four supported types, it becomes `chosen` directly — the function does not run it through
`avoid_workout_types` filtering the way the TSB-derived preference order already does. It still computes
what the TSB-derived default *would* have been (`preferences[0]`), and sets a conflict note whenever
`chosen != preferences[0]`.

**Rationale**: this is the FR-009 decision made in conversation with the athlete — a same-turn, explicit
request is more specific and more recent than a durable dislike note, and the existing standing-preference
mechanism (`avoid_workout_types`, spec 009) is left completely untouched for every other call where no type
is named. Running the request through the avoid-list would reintroduce exactly the "make me fight the bot"
problem this feature exists to remove.

**Alternatives considered**: treating an explicit request as merely the top of the preference list (still
filtered by `avoid_workout_types`). Rejected — that would silently refuse the athlete's own request if they
had *ever* told the coach they dislike that type, the opposite of FR-002/FR-009.

## Decision 4: No new duration parameter on the engine side — reuse `available_minutes`

**Decision**: `build_freestyle_suggestion()` already accepts `available_minutes: int | None`, threaded
straight into `fitting.fit_template()`. `max_duration_minutes` extracted by the LLM is passed through
exactly there — no new engine parameter, no new fitting logic.

**Rationale**: the ceiling-fitting behavior FR-006 asks for already exists; it was simply never fed by
anything but `None` from the freestyle tool. `NoSuitableTemplateError` (raised when no candidate fits even
after `_fit_with_relaxed_tolerance()`'s three tolerance passes) already becomes `"available": False` with a
plain reason in `_tool_get_freestyle_session_suggestion` — the exact behavior FR-006's second acceptance
scenario asks for.

## Decision 5: An invalid or mismatched `template_id` is a silent fallback, not a new error path

**Decision**: `app/llm/chat.py::_tool_get_freestyle_session_suggestion` validates that a supplied
`template_id`, if present, belongs to the candidate set for the workout type `choose_workout_type()`
actually resolved (which may itself have changed if `requested_workout_type` was also supplied). If it
doesn't — wrong type, unknown id, or omitted — it is passed as `None` to `build_freestyle_suggestion()`,
which already falls back to its existing `day_ordinal` rotation. No exception, no error surfaced to the
athlete for this specific case.

**Rationale**: FR-005 requires the system to "reject" an out-of-set choice, but rejecting only needs to mean
"don't trust it" — the athlete never sees an out-of-band selection, since it was never their words, only a
model-side slip. Surfacing an error here would blame the athlete for a mismatch they didn't cause. This
keeps `build_freestyle_suggestion()`'s existing signature (already accepts a `template_id`-shaped override
sitting in front of the rotation) untouched in spirit — see Decision 6.

**Alternatives considered**: raising and surfacing a distinct error state. Rejected — this path exists to
protect against the model naming a plausible-but-wrong id, not a real athlete-facing condition; the current
"available: False" reason path is reserved for genuine no-fit situations (Decision 4), which stays clearer
if this case doesn't also route through it.

## Decision 6: The conflict note is data on `WorkoutTypeChoice`, appended to `reasoning_summary` — same shape as the existing `preference_overridden` note

**Decision**: `WorkoutTypeChoice` gains a `default_conflicts: bool` field (True whenever `chosen !=
preferences[0]`, independent of `preference_overridden`, which is about the avoid-list case only).
`build_freestyle_suggestion()` appends a sentence to `reasoning_summary` when `default_conflicts` is True,
following the exact pattern already used for `preference_overridden`'s note (freestyle_selector.py:200-204)
— one `if` block, one appended sentence, no new response field.

**Rationale**: `reasoning_summary` is already the single channel the LLM reads to narrate *why* a session
was chosen (never a channel for numbers — those stay in `target_tss`/`duration_minutes`, Constitution
Principle I). Adding a second, differently-named boolean-plus-sentence pair alongside the existing one keeps
the two conflict sources (avoid-list vs. explicit-request-vs-default) distinguishable in tests without
inventing a new response shape.
