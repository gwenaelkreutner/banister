# Contract: `get_freestyle_session_suggestion` LLM tool

Follows the existing tool-schema convention in `app/llm/tools.py` (`TOOL_DEFINITIONS`) and dispatch
convention in `app/llm/chat.py`'s `_execute_tool`.

## Tool definition (added to `TOOL_DEFINITIONS`)

```json
{
  "type": "function",
  "function": {
    "name": "get_freestyle_session_suggestion",
    "description": "Propose une séance adaptée à la forme actuelle de l'athlète, sans référence à un plan (mode libre uniquement). Utilise cet outil quand l'athlète demande quoi faire aujourd'hui / une séance / un conseil d'entraînement alors qu'il n'a pas d'objectif actif.",
    "parameters": {
      "type": "object",
      "properties": {},
      "required": []
    }
  }
}
```

No parameters: the tool takes nothing from the athlete's message. Every input it needs (fitness state,
recent load, declared preferences) is already available server-side — mirroring why `get_upcoming_sessions`
is the only existing tool with a caller-supplied parameter (`days`) and every other tool's inputs come from
what the athlete already said in the conversation, never invented figures.

## Availability

Only present in the tool list passed to the LLM (`chat.py:211`) when `coaching_mode(user) == "freestyle"`
(Research Decision 6). Absent entirely in goal mode — the model is never given the option to call it there.

## Result shape (function output fed back to the model)

```json
{
  "available": true,
  "workout_type": "endurance",
  "duration_minutes": 90,
  "target_tss": 62,
  "zone_code": "Z2",
  "reasoning_summary": "TSB -6, charge des 7 derniers jours modérée : séance d'endurance pour construire sans creuser la fatigue."
}
```

Or, when there isn't enough history (FR-011):

```json
{
  "available": false,
  "reason": "Pas encore assez de données de forme (moins de 7 jours d'historique) pour proposer une séance adaptée."
}
```

The model's role is strictly to phrase this result conversationally (Principle I) — it must not restate
`target_tss`/`duration_minutes`/`zone_code` with different numbers than what the tool returned. This is
already governed by the existing response-verification pass (`app/services/response_verification.py`,
spec 006 US3): these fields register as retrievable metrics for that verifier the same way plan-session
figures already do, so a misstated number here is caught the same way a misstated CTL/ATL figure is today.

## Non-goals

- No follow-up "adjust this suggestion" tool in this feature — an athlete who wants something different
  asks again in the next message (US1 doesn't require the tool to be stateful).
- No persistence of the suggestion (see data-model.md) — nothing to look up later, nothing to match a
  subsequent activity against.
