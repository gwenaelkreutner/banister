# Phase 0 Research: Publish a Freestyle Session

## Decision 1: Reuse the existing pending_proposal → button plumbing, not a new mechanism

**Decision**: `app/bot/routers/chat.py` already has exactly the shape this feature needs:
`app/llm/chat.py`'s chat function returns a `pending_proposal` dict whenever a tool's result should be
confirmed before acting (today: `propose_plan_modification`, `propose_session_adjustment` — see
`chat.py`'s `pending_proposal = last_tool_result` block and `chat.py:96-105`'s
`InlineKeyboardButton(text="✅ Appliquer", callback_data="chat:apply")`). Extend the same tagging: when
`tool_used == "get_freestyle_session_suggestion"` and the result has `available: true`, it becomes a
`pending_proposal` too, tagged `"type": "freestyle_publish"` (mirroring how
`_tool_propose_session_adjustment` already tags its own shape with `"type": "session_adjustment"` so
`cb_apply_modification` can tell the two proposal shapes apart, `chat.py:153`).

**Rationale**: the spec's confirmed answer (Q1: button, no new LLM tool) is exactly what this plumbing
already does for the two existing proposal types. No new mechanism to design or explain to the athlete —
they've already seen a confirm/cancel button for a plan change; this is the same interaction for a
different kind of proposal.

**Alternatives considered**: a parallel confirmation mechanism specific to freestyle. Rejected — would give
the athlete two different button idioms for "confirm this" in the same product.

## Decision 2: Negotiation must NOT block chat the way plan-modification approval does

**Decision**: `PlanStates.PENDING_MODIFICATION` (used for the two existing proposal types) blocks ordinary
chat until the athlete taps Apply or Cancel (`chat.py:121-127`,
`handle_message_during_pending_modification`). A freestyle publish proposal must **not** do this — US2
requires the athlete to be able to keep asking for a different session mid-negotiation, which is ordinary
chat. The pending suggestion is tracked in FSM **data** (`state.update_data(...)`) without changing the FSM
**state** away from `PlanStates.ACTIVE`/`None`, so `handle_chat_message`'s existing
`StateFilter(PlanStates.ACTIVE, None)` keeps matching every message throughout negotiation.

**Rationale**: found by reading the exact blocking mechanism spec 007/general chat already relies on for
the other two proposal types — it is deliberate there (a plan edit needs a clean apply/cancel decision
before more requests pile up) but wrong here (FR-001/US2 explicitly wants free negotiation).

## Decision 3: A suggestion identifier, not just "the last one", to satisfy FR-005/FR-005a

**Decision**: Each suggestion shown gets a short id (e.g. `uuid4().hex[:8]`) generated when the tool
computes it. The button's `callback_data` encodes that id (`freestyle:publish:<id>`). FSM data stores
`{"id": ..., "suggestion": {...}}` — only ever one entry, overwritten every time a new suggestion is shown
(Assumptions: one pending suggestion at a time). The callback handler compares the tapped id against the
currently-stored one: mismatch (or nothing stored) means stale/expired → tell the athlete plainly (FR-005),
never publish the content sitting under an old button.

**Rationale**: Telegram does not remove old inline keyboards on its own — if the athlete asked for a second
suggestion (US2) but the first message's button is still visible above in the chat history, tapping it must
not resurrect and publish the superseded content. An id comparison makes "superseded" detectable without
tracking every past message.

## Decision 4: A new, smaller table — not `PublishedEntry` — for freestyle publications

**Decision**: `PublishedEntry` (spec 005) has `plan_id` and `approval_id` **NOT NULL**
(`app/db/models/publication.py`) plus mandatory `week_number`/`day_of_week` — exactly the same shape of
problem spec 009 hit with `SessionLog.plan_id` (Decision 3 there). A freestyle publication belongs to no
plan and was never batch-approved. Rather than retrofit more nullable columns onto a table whose name and
shape are about plan-horizon publication, add a new table, `freestyle_published_entries`
(`user_id`, `external_id`, `intervals_event_id`, `session_date`, `workout_type`, `content_hash`,
`published_at`, `withdrawn_at`) — no `plan_id`, no `approval_id`, no `week_number`/`day_of_week`, because
none of those exist for a freestyle session.

**Rationale**: Constitution Principle IV — a freestyle session did not happen under a plan or a batch
approval, and forcing it into a schema shaped for both would be exactly the "silent fiction" spec 009's
Decision 3 already rejected once for `SessionLog`. Two smaller, honest tables are clearer than one
overloaded one.

**Alternatives considered**: relaxing `PublishedEntry.plan_id`/`.approval_id` to nullable, same technique as
spec 009's migration. Rejected — `week_number`/`day_of_week` would also need relaxing for no benefit, and
`published_entries`' existing indexes/uniqueness are keyed around plan semantics
(`idx_published_entries_plan`) that a freestyle row would never use.

## Decision 5: No separate "approval" phase — the confirmed content IS the record

**Decision**: Spec 005 splits request → approval (`PublicationApproval`, a distinct pending/approved/
declined record) from execution, because a plan-horizon publish can review many sessions before the
athlete decides. Here there is exactly one session and one button; approval and execution are the same
tap. `freestyle_published_entries` is written **at** publish time with the content hash of exactly what was
rendered — that row itself is the audit trail (SC-002: it can always be compared back to the last
suggestion shown), no separate approval table needed.

**Rationale**: matches the actual shape of the interaction (single button, not a multi-session review
screen) — introducing a second table purely for symmetry with spec 005 would be complexity with no
guarantee it actually strengthens.

## Decision 6: DSL rendering and event payload are already plan-agnostic — reuse verbatim

**Decision**: `workout_dsl.render_dsl(steps, zones)` and `calendar.build_event_payload(date, name,
external_id, rendered_dsl)` (`app/providers/intervals/calendar.py`) take no plan-shaped argument at all —
only `build_external_id()` and `publish_sessions()`'s horizon-diffing loop are plan-specific. Add a sibling
`build_freestyle_external_id(session_date, workout_type)` (still prefixed `banister:`, so FR-006's
ownership guarantee holds the same way spec 005's does) and call `client.create_event()` directly for a
single write — no diffing needed, since a freestyle publish is always a fresh create, never an update of an
existing entry (Assumptions: confirming an already-published suggestion again is a no-op, FR-010, not a
second create).

**Rationale**: minimizes new code — the only genuinely new I/O is a two-field external id builder and a
single `create_event` call; everything else (DSL text, hashing) is exact reuse.

## Decision 7: Zones must be computed fresh from the profile, not read from a plan

**Decision**: `render_dsl()` needs a `zones: dict[str, Zone]` mapping. In goal mode this comes from
`TrainingPlanSchema.zones` (computed once at plan generation). Freestyle mode has no plan, so compute it at
publish time from the athlete's profile via the existing `app/engine/zones.py::compute_power_zones(ftp)` /
`compute_hr_zones(hr_max, hr_rest)` — the same functions `plan_builder.py` already calls, just invoked at a
different time.

**Rationale**: no new zone logic; these functions were already generic (a plan is not one of their
parameters today either).
