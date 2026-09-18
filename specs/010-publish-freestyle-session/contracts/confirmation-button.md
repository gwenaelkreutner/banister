# Contract: freestyle publish confirmation button

Extends the existing `pending_proposal` mechanism in `app/bot/routers/chat.py` /
`app/llm/chat.py` (Research Decision 1) rather than adding a new one.

## Tool result tagging

`_tool_get_freestyle_session_suggestion` (spec 009) gains one field when `available: true`:

```json
{
  "available": true,
  "type": "freestyle_publish",
  "id": "a1b2c3d4",
  "workout_type": "endurance",
  "duration_minutes": 90,
  "target_tss": 62,
  "zone_code": "Z2",
  "reasoning_summary": "..."
}
```

`"type": "freestyle_publish"` mirrors how `_tool_propose_session_adjustment` already tags its own shape
(`"type": "session_adjustment"`) so the router's callback handler can tell proposal shapes apart. `"id"` is
new — an 8-hex-char id (`uuid.uuid4().hex[:8]`) generated fresh each time a suggestion is computed.

## Router change (`app/bot/routers/chat.py`)

`run_chat()`'s existing `pending_proposal` detection (today: `tool_used in
("propose_plan_modification", "propose_session_adjustment")`) gains `"get_freestyle_session_suggestion"`
to that tuple, guarded by `last_tool_result.get("available")` (an unavailable suggestion — FR-011 of spec
009 — is never a proposal to confirm).

When `pending_proposal.get("type") == "freestyle_publish"`:

1. Store `pending_freestyle_id = proposal["id"]` and `pending_freestyle_suggestion = proposal` in FSM
   **data** — critically, **do not** call `state.set_state(PlanStates.PENDING_MODIFICATION)` (Research
   Decision 2) — the state stays `ACTIVE`/`None` so ordinary chat (asking for a different session) keeps
   working.
2. Send the narrated suggestion text with one button:
   `InlineKeyboardButton(text="📅 Publier sur intervals.icu", callback_data=f"freestyle:publish:{id}")`.

## Callback handler (new)

```
@router.callback_query(F.data.startswith("freestyle:publish:"))
```

Not scoped to a special `StateFilter` (Research Decision 2 — negotiation never leaves `ACTIVE`/`None`).

1. Extract the tapped id from `callback_data`.
2. Compare against `pending_freestyle_id` in FSM data.
   - Missing, or mismatched (a newer suggestion superseded it, Research Decision 3): `callback.answer("Cette
     proposition n'est plus la plus récente — redemande une séance.", show_alert=True)`. Nothing written
     (FR-005).
   - Match: proceed.
3. Compute zones from the athlete's profile (Research Decision 7), render the DSL, build the external id
   (Research Decision 6), write the event (`contracts/single-session-write.md`).
4. On success: `callback.message.edit_text(...)` confirming publication, with the date and a note that this
   is a one-off entry (not part of a plan). Clear `pending_freestyle_id`/`pending_freestyle_suggestion`
   from FSM data (FR-010 — a second tap after this has nothing to match).
5. On failure: `callback.message.edit_text(...)` stating the publish did not go through (FR-008) —
   `pending_freestyle_id` is **not** cleared, so retapping is a legitimate retry, not a stale-id rejection.

## Non-goals

- No natural-language path to confirm ("publie-la" typed in chat) — Clarification Q1 chose the button
  specifically to make FR-002 unambiguous by construction; a text-based alias would reopen exactly the
  ambiguity that was rejected (Option C).
- No editing a suggestion via the button (e.g. "shorter please") — that negotiation stays in chat, before a
  button ever needs to be tapped (US2).
