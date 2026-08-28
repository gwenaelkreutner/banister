# Data Model: Publishing Planned Sessions to the Athlete's Calendar

**Feature**: 005-planned-workout-push | **Date**: 2026-08-28

Entities from [spec.md](./spec.md) §Key Entities, resolved against what the live API actually provides
([research.md](./research.md)).

Unlike spec 004 — which was pure Pydantic-in-JSON and needed no migration — **this feature requires two
real tables and an Alembic revision**. The reason is FR-016: an interrupted publication must be resumable,
which means what we published has to survive a restart. Holding it in memory or re-deriving it from the
calendar alone would lose the approval linkage FR-005 requires.

---

## PublicationApproval

The athlete's recorded consent to write a specific set of sessions on specific dates. **Bounded to what
was shown** (FR-004) — this is the entity that makes "was this written with consent?" answerable after
the fact (spec 001 FR-019a).

| Column | Type | Rule |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | FK → `users.id`, cascade | |
| `plan_id` | FK → `training_plans.id`, cascade | which plan the approved sessions came from |
| `content_hash` | `String(64)` | SHA-256 over exactly what the athlete was shown — see §Content hashing |
| `horizon_start` / `horizon_end` | `Date` | the bounded period (FR-010), recorded so it can be stated back |
| `session_count` | `SmallInteger` | how many sessions were in the request, for the summary report (FR-006) |
| `status` | `String(16)` | `"pending"` \| `"approved"` \| `"declined"` |
| `requested_at` | `UtcDateTime` | |
| `decided_at` | `UtcDateTime \| None` | set when approved or declined |

**States**: `pending` → `approved` or `declined`. Terminal either way. A declined approval is kept, not
deleted — FR-003 requires not re-asking unprompted, and a deleted record cannot express "they already
said no."

**FR-004 enforcement**: publication compares the plan's *current* content hash against the approval's.
If they differ, the approval does not authorize the write, and fresh approval is required. This is
checked at publication time, not only at request time — the plan can change in between.

## PublishedEntry

One session written to the athlete's calendar, identifiable as ours and traceable to its planned session
(FR-013).

| Column | Type | Rule |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | FK → `users.id`, cascade | |
| `plan_id` | FK → `training_plans.id`, cascade | |
| `approval_id` | FK → `publication_approvals.id` | **which approval authorized this write** (FR-005) |
| `external_id` | `String(128)`, unique per user | our ownership marker — see §External id |
| `intervals_event_id` | `String(32)` | the remote event id, required for `PUT`/`DELETE` (R2) |
| `session_date` | `Date` | the planned date the entry sits on |
| `week_number` / `day_of_week` | `SmallInteger` | traceability back to the `SessionSpec` (FR-013) |
| `content_hash` | `String(64)` | hash of what we actually wrote — the basis for divergence detection (FR-020) |
| `published_at` | `UtcDateTime` | |
| `withdrawn_at` | `UtcDateTime \| None` | set on withdrawal (FR-021); the row is kept as history |

**Why keep withdrawn rows**: FR-024 says a published entry the athlete deleted must not be silently
recreated. Distinguishing "we withdrew this" from "the athlete deleted this" from "we never published
this" needs all three states to be representable, and deleting the row collapses two of them.

## External id

```
banister:<plan_id_short>:<yyyy-MM-dd>:<workout_type>-<week>-<dow>
```

- **`banister:` prefix** — deliberately distinct from enduragent's `cycling-coach:`, which is *already
  present in this athlete's real calendar* (research R5). Every read-for-diff, update and delete filters
  on this prefix, which is what makes FR-015 ("never modify entries it did not create") true by
  construction rather than by care.
- **plan id** scopes entries to the plan that produced them, so a wholesale plan regeneration (spec edge
  case) leaves the previous plan's entries identifiable and withdrawable rather than orphaned.
- **date + week + day-of-week** makes the entry traceable back to the exact `SessionSpec` (FR-013).

## Content hashing

One rule, used for both entities:

```
SHA-256( session_date | name | rendered_DSL_text )
```

The rendered DSL is what actually reaches the calendar and what the athlete is shown in the approval
request — so hashing it means the approval is bound to precisely the content it was shown for (FR-004),
and a published entry can be compared against the current plan to detect drift (FR-020) with no
ambiguity about which fields "count".

Deliberately **not** included: `intervals_event_id` (server-assigned, not content), `approval_id`
(provenance, not content), and load/duration (derived by intervals.icu from the DSL itself — R4 — so
including them would double-count the same information).

## Publication horizon

Not stored as an entity. Computed as *current week + following week* from the plan's start date
(spec assumption, FR-010), recorded on each approval as `horizon_start`/`horizon_end` so it can be
stated back to the athlete rather than being an invisible constant.

## Divergence

Not stored — **derived**, always, on demand. Three kinds, each detected differently:

| Kind | Detection | Requirement |
|---|---|---|
| Plan moved ahead of calendar | current session's content hash ≠ `PublishedEntry.content_hash` | FR-020 |
| Athlete edited our entry | remote event's content ≠ `PublishedEntry.content_hash` | FR-023 |
| Athlete deleted our entry | `external_id` absent from the remote window but a live `PublishedEntry` exists | FR-024 |

Deriving rather than storing is deliberate: a stored divergence flag is a cache that goes stale exactly
when it matters most — after a change nobody told it about.

## What this feature does *not* model

`SessionSpec`, `Step` and `RepeatGroup` are unchanged (spec 004). This feature reads them and renders
them; it never constructs or modifies one. `app/engine/` is untouched — see plan.md's Constitution Check.
