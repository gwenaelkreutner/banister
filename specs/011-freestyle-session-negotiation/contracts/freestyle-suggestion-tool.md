# Contract: `get_freestyle_session_suggestion` parameters

Extends the existing tool (spec 009, tagged for confirmation by spec 010) with three optional parameters —
no new tool, no new response field beyond what Research Decision 6 already appends to `reasoning_summary`.

## Tool schema (`app/llm/tools.py`)

```json
{
  "name": "get_freestyle_session_suggestion",
  "parameters": {
    "type": "object",
    "properties": {
      "requested_workout_type": {
        "type": "string",
        "enum": ["long_ride", "intervals", "endurance", "recovery"],
        "description": "Type de séance demandé EXPLICITEMENT par l'athlète dans ce message (ex: 'je veux faire des intervalles'). Omettre si l'athlète n'a rien demandé de précis — le choix se fera alors selon sa forme du moment. N'invente jamais une correspondance avec un type non listé ici (ex: une demande de 'yoga' ne doit PAS être mappée sur un des 4 types — dis-le à l'athlète à la place)."
      },
      "max_duration_minutes": {
        "type": "integer",
        "minimum": 15,
        "maximum": 300,
        "description": "Durée maximale disponible mentionnée par l'athlète pour cette séance précise. Omettre si rien n'est mentionné."
      },
      "template_id": {
        "type": "string",
        "enum": ["<built at import time from session_library.load_library(), one entry per SessionTemplate.id>"],
        "description": "Choisis un id UNIQUEMENT si l'athlète exprime une préférence de style sur le contenu de la séance (ex: 'pas de pyramide', 'plutôt du steady') ET qu'un des templates listés correspond clairement. La liste ci-dessous donne, par type de séance, chaque id avec son 'purpose'/'intent'/'suits'. Omettre si aucune préférence de style n'est exprimée, ou si aucun template ne correspond clairement — un mauvais choix est silencieusement ignoré, mieux vaut ne rien forcer."
      }
    },
    "required": []
  }
}
```

The `template_id` enum and its description body are generated once, at module import, by grouping
`session_library.load_library()`'s templates by `workout_type` and listing `id — purpose — intent — suits`
per template (Research Decision 2). This file does not reproduce that generated text since it is
library-derived and changes whenever `sessions/*.yaml` changes.

## Call-site change (`app/llm/chat.py`)

`_execute_tool`'s dispatch for `"get_freestyle_session_suggestion"` gains `args=args` (today it calls
`_tool_get_freestyle_session_suggestion` with no `args` at all — the only tool handler that doesn't receive
its own arguments, because it never needed any until now).

`_tool_get_freestyle_session_suggestion(*, args: dict, user, session, profile, logs, activities)`:

1. Read `requested_workout_type = args.get("requested_workout_type")` — passed straight to
   `choose_workout_type()`/`build_freestyle_suggestion()` (Research Decision 3); no server-side mapping
   attempted beyond the enum the schema already constrains it to.
2. Read `max_duration_minutes = args.get("max_duration_minutes")` — forwarded as `build_freestyle_suggestion`'s
   existing `available_minutes` parameter (Research Decision 4).
3. Read `raw_template_id = args.get("template_id")`. After `build_freestyle_suggestion()` has resolved a
   `workout_type` (see step below), validate `raw_template_id` is a member of that type's own candidate ids
   (`session_library.load_library()` filtered by `workout_type`); if not a member, pass `None` instead
   (Research Decision 5) — never surfaced to the athlete as an error.
4. Forward `requested_workout_type`, `requested_template_id` (validated), and `available_minutes` to
   `build_freestyle_suggestion()` exactly as its existing `avoid_workout_types`/`day_ordinal` parameters are
   forwarded today.

Steps 1 and 3 have an ordering dependency: which workout type template ids are valid for depends on which
type gets resolved, which itself can be the explicit request from step 1. `build_freestyle_suggestion()`
already resolves the type internally (via `choose_workout_type()`) before ever touching the template
rotation — the validation in step 3 reuses that same resolved value rather than re-deriving it, so no type
resolution logic is duplicated in `chat.py`.

## Response shape — unchanged

```json
{
  "available": true,
  "type": "freestyle_publish",
  "id": "a1b2c3d4",
  "workout_type": "intervals",
  "duration_minutes": 60,
  "target_tss": 58,
  "zone_code": "Z4",
  "reasoning_summary": "TSB -14, charge des 7 derniers jours 210 TSS → séance de type endurance. Ce n'est pas ce que je t'aurais proposé spontanément (effort dur il y a 1 jour), mais voici une séance d'intervalles adaptée à ta forme actuelle.",
  "steps": ["..."]
}
```

Only `reasoning_summary`'s text changes (a new appended sentence, Research Decision 6) — every numeric
field is produced exactly as before, by the same engine code, regardless of which parameters were supplied.

## Non-goals

- No second tool call to "confirm" a template choice before the suggestion is generated — Decision 1
  explicitly keeps this to one call.
- No change to how a confirmed suggestion is published (spec 010's button/callback flow is untouched — it
  consumes whatever `FreestyleSuggestion` this tool returns, negotiated or not).
- No change to the durable `disliked_workout_types` preference — this contract only ever reads it
  (unchanged), never writes it.
