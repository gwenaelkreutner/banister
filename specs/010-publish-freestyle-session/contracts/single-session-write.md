# Contract: writing one freestyle session to the calendar

New, small functions alongside the existing plan-horizon publish path — not a modification of it
(`app/providers/intervals/calendar.py`, `app/services/publication.py` stay as spec 005 left them).

## `build_freestyle_external_id(session_date, workout_type)` (new, `calendar.py`)

```python
def build_freestyle_external_id(session_date: date, workout_type: str) -> str:
    """banister:freestyle:<yyyy-MM-dd>:<workout_type>-<id8> — same ownership prefix as
    build_external_id(), no plan_id because there is no plan (spec 010)."""
    return f"{EXTERNAL_ID_PREFIX}freestyle:{session_date.isoformat()}:{workout_type}-{uuid.uuid4().hex[:8]}"
```

Reuses `EXTERNAL_ID_PREFIX` (`"banister:"`) so FR-006's ownership guarantee — a withdrawal action never
touches an entry we did not create — holds for exactly the same reason it already holds for plan-published
entries (the prefix check, not per-table bookkeping).

## `publish_freestyle_session(client, session_date, name, workout_type, steps, zones)` (new,
service-layer, `app/services/publication.py` or a new small module — plan.md decides the exact file)

```python
async def publish_freestyle_session(
    client: IntervalsClient,
    session_date: date,
    name: str,
    workout_type: str,
    steps: list[Step | RepeatGroup],
    zones: dict[str, Zone],
) -> SessionOutcome:
    """Always a create — a freestyle publish has nothing to diff against (Research
    Decision 6). Raises EmptySessionError the same way publish_sessions() does for a
    session with no renderable content; the caller (the callback handler) turns that
    into the FR-008 failure message, never a silent no-op."""
```

Internally: `render_dsl(steps, zones)` → `hash_session_content(session_date, name, rendered)` →
`build_freestyle_external_id(session_date, workout_type)` → `calendar.build_event_payload(...)` →
`client.create_event(payload)`. Returns the same `SessionOutcome` shape `publish_sessions()` already
returns, for one entry, so the caller's success/failure handling looks like the existing pattern.

**No `push_errors` special-casing beyond what `publish_sessions()` already established**: if
`create_event()`'s response carries `push_errors`, delete the just-created event and report `"refused"`,
exactly like `publish_sessions()`'s existing handling — do not duplicate that logic differently here.

## `withdraw_freestyle_publications(session, client, user)` (new, mirrors `withdraw_all_publications`)

```python
async def withdraw_freestyle_publications(
    session: AsyncSession, client: IntervalsClient, user: User,
) -> tuple[int, int]:
    """Every still-future, not-yet-withdrawn row in freestyle_published_entries for this
    user. Called from two places: US3's explicit withdrawal, and app/bot/routers/goal.py's
    freestyle→goal transition (FR-011), the same way withdraw_all_publications() is
    already called from both /unpublish and goal→freestyle (spec 009)."""
```

Iterates the new table only — never touches `published_entries` (plan-mode), by construction (a different
table, not a filter that could be gotten wrong).

## Persistence (`freestyle_publication_repo.py`, new repository, mirrors `publication_repo.py`'s shape)

- `create(session, user_id, external_id, intervals_event_id, session_date, workout_type, content_hash,
  published_at)`
- `get_active_for_user(session, user_id)` — not-yet-withdrawn rows, for US3's withdrawal and FR-011's
  mode-switch withdrawal
- `mark_withdrawn(session, entry_id)`

## Non-goals

- No diff/update/conflict handling (`publish_sessions()`'s US5 machinery) — a freestyle session is never
  re-published over itself; confirming twice is a no-op (FR-010) checked before any write is attempted, not
  a content comparison against a remote event.
