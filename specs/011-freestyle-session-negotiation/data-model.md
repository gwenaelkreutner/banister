# Phase 1 Data Model: Freestyle Session Negotiation

No new database table or column — FR-010 requires nothing introduced here to outlive a single
request/response exchange, consistent with freestyle suggestions never being persisted today. Every shape
below is an in-memory dataclass/dict, alive only for the duration of one tool call.

## `WorkoutTypeChoice` (extended, `app/engine/freestyle_selector.py`)

| Field | Type | Notes |
|---|---|---|
| `workout_type` | str | unchanged — the resolved type, either the explicit request (Research Decision 3) or the TSB-derived default |
| `target_tss` | float | unchanged — always computed from `fitness.ctl`, never from the request |
| `reasoning_summary` | str | unchanged shape, now may carry two independent appended notes: the existing avoid-list override note, and the new default-conflict note (Research Decision 6) |
| `preference_overridden` | bool | unchanged — True only when every preferred type was in `avoid_workout_types` |
| `default_conflicts` | bool | **new** — True whenever `requested_workout_type` was supplied and differs from what the TSB-derived preference order would have chosen unprompted (FR-002) |

## `FreestyleSuggestion` (unchanged shape, new inputs to `build_freestyle_suggestion()`)

No new fields on the returned dataclass — `requested_workout_type`/`requested_template_id` affect *how*
`workout_type`/`template_id`/`steps`/`duration_minutes`/`target_tss` are produced, not the shape of the
result. Callers (the LLM tool layer) cannot tell from the response alone whether a value was requested or
defaulted, except through `reasoning_summary`'s conflict note — by design, since the number itself must
never differ based on who asked for it (Constitution Principle I).

## `get_freestyle_session_suggestion` tool parameters (new, `app/llm/tools.py`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `requested_workout_type` | enum of `VALID_WORKOUT_TYPES` (`long_ride`, `intervals`, `endurance`, `recovery`) | no | omitted when the athlete named no specific type this turn (FR-007 fallback) |
| `max_duration_minutes` | integer | no | forwarded verbatim to `build_freestyle_suggestion(available_minutes=...)` (Research Decision 4) |
| `template_id` | enum of every `SessionTemplate.id` in `session_library.load_library()`, built at import time (Research Decision 2) | no | validated server-side against the *resolved* workout type's own candidates before use; a mismatch is silently ignored (Research Decision 5), never surfaced as an error |

All three are independently optional — a request naming only a duration ceiling, or only a type, or
nothing at all, must all continue to work (FR-007).

## Relationship to spec 009 entities

- Extends spec 009's `choose_workout_type()`/`build_freestyle_suggestion()` in place — no parallel
  "negotiated suggestion" type is introduced; a negotiated and a default suggestion are the same
  `FreestyleSuggestion` shape, just reached with different inputs.
- Leaves the durable `athlete_notes["disliked_workout_types"]` preference (spec 009, read by
  `_tool_get_freestyle_session_suggestion` today) completely untouched — this feature reads it exactly as
  before (`avoid_workout_types`) and lets `requested_workout_type` bypass it only for the current call
  (Research Decision 3), never writes to it.
