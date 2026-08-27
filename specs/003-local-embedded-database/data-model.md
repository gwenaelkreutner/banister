# Phase 1 Data Model: Local Embedded Database

**Feature**: 003-local-embedded-database | **Date**: 2026-08-27

This feature does not introduce, remove, or reshape any entity. The spec excludes schema redesign, so this
document records **what exists and what changes about how it is stored** — the portability delta, table by
table.

---

## Portability deltas applied across all tables

These three changes are mechanical, apply everywhere the pattern occurs, and land while still running on
PostgreSQL (plan phase A).

| Current | Becomes | Why | Requirement |
|---|---|---|---|
| `postgresql.UUID(as_uuid=True)` | `sqlalchemy.Uuid(as_uuid=True)` | Dialect-neutral; renders `CHAR(32)` on the target while still returning `uuid.UUID` in Python | FR-012 |
| `postgresql.JSONB` | `sqlalchemy.JSON` | Dialect-neutral; verified to round-trip nesting, types, and empty-versus-absent | FR-007 |
| `DateTime(timezone=True)` | `UtcDateTime` (new type decorator) | The target discards offsets; the decorator normalises on write and re-attaches UTC on read | FR-010 |
| `server_default=func.now()` | portable default | `now()` renders differently per backend; the timestamp mixin must produce the same instant either way | FR-010 |
| `server_default="true"` | portable boolean default | Booleans are stored as 0/1 on the target | FR-011 |

**Invariant that must hold after every one of these**: a value that was unknown stays distinguishable from
zero, from false, and from an empty document (FR-011). This is the assertion most likely to pass by
accident in a happy-path test and fail in production, because `None`, `0`, `False` and `{}` are all falsy
in Python and only differ once read back from storage.

---

## Entities

Eight tables, all owned by a single athlete. `user_id` is a foreign key to `users` with
`ondelete="CASCADE"` in every case except `users` itself — which is why R1's pragma finding governs the
whole model rather than one table.

### `users`

The single athlete's Telegram identity and reminder preferences.

- Identity: `id` (UUID), Telegram identifier
- Reminder preferences: enabled flag, hour, minute, last-sent date
- **Delta**: UUID type; boolean `server_default`; timestamp mixin

### `athlete_profiles`

The athlete's profile, coaching memory, and notes.

- `profile` — structured document validated as `AthleteProfileSchema`
- `coach_memory` — list document, defaults to `[]`
- `athlete_notes` — object document, defaults to `{}`
- **Delta**: UUID; three JSON documents; the `[]`-versus-`{}` defaults are currently expressed as
  PostgreSQL-cast literals and must become portable without changing the distinction between an empty
  document and an absent one

### `training_plans`

Generated plans. `plan_technical` is validated as `TrainingPlanSchema`; `plan_narrative` holds the
narration.

- **Delta**: UUID; two JSON documents
- **Carry-over**: locally originated and irreplaceable (FR-023)
- **Note for later**: spec 004 adds structured steps inside `plan_technical`. Because it is a document
  column, that is not a schema change here — which is precisely why this port must not disturb document
  fidelity.

### `session_logs`

The largest table: 30 columns covering the completed session, its quality metrics, and its Strava context.

- Identity and linkage: `id`, `user_id`, `plan_id` (both cascade), `week_number`, `day_of_week`,
  `logged_date`
- Outcome: `status`, `rpe_emoji`, `duration_minutes_actual`, `tss_actual`, `source`
- Measured: `avg_heart_rate`, `avg_power`, `normalized_power`, `kilojoules`
- Quality: `cardiac_drift_index`, `intervals_consistency_index`, `respect_zones_score`,
  `session_type_real`, `variability_index`, `intensity_factor`, `dominant_zone`
- Context: `elevation_gain_m`, `average_temp_c`, `athlete_count`
- Fitness at the time: `ctl_at_session`, `atl_at_session`
- `time_in_zones_s` — JSON document
- **Delta**: UUID ×3; one JSON document; `logged_date` is a date, not a timestamp, and must stay one
- **Highest-risk table for FR-011**: most columns are nullable floats. A metric that was genuinely not
  measured must not become `0.0`, because these feed chronic load and the adherence score.

### `activities`

Imported activity history, with the load value and the method used to derive it.

- `tss`, `tss_method`, `ftp_used`, `device_watts`, plus measured values
- **Delta**: UUID ×2; boolean `device_watts` nullable — the three-state distinction between "measured with
  a power meter", "not measured with one", and "unknown" must survive
- **Carry-over**: source-derived, therefore re-fetchable rather than transferred (FR-024)

### `chat_messages`

Conversation history: role, content, intent, tool used.

- **Delta**: UUID ×2
- **Carry-over**: locally originated and irreplaceable (FR-023)

### `weekly_adherence`

One row per athlete per week, upserted on each weekly review.

- Key: `(user_id, week_start_date)`
- Values: sessions done, sessions planned, compliance percentage, 7-day load, week number, plan id
- **Delta**: UUID; **upsert construct** — one of the three repositories affected by R4
- **Carry-over**: locally originated and irreplaceable (FR-023)

### `oauth_connections`

Credentials for the delegated-authorization provider integration.

- **Delta**: UUID ×2; **upsert construct** (R4)
- **Lifecycle note**: this table belongs to the provider integration that spec 002 removes. Spec 002 is
  built *after* this one, so the table must survive this port intact and be dropped later, by a migration
  that this feature's mechanism makes possible. Dropping it here would put a spec-002 change inside a
  spec-003 commit.

---

## New persistent state introduced by this feature

Two items, neither of which is athlete data.

### Schema version marker

Records which structural revision the store is at, so the running software can determine what to apply and
can refuse to run against a revision it does not know (FR-021, FR-022). Managed by the migration tool
rather than hand-rolled.

### Instance lock

An advisory lock held for the lifetime of a running instance, preventing a second instance from operating
against the same data directory (FR-005). Not a table — a lock file, so that a crashed process releases it
by dying rather than leaving a stale row indistinguishable from a live one.

---

## What is explicitly NOT changing

Recorded so that a reviewer can check the diff against this list:

- No table is added, removed, renamed, split, or merged
- No column is added, removed, renamed, or retyped in a way that changes its meaning
- No relationship changes
- No index or constraint is added or removed except as required to express the existing ones portably
- The consolidation opportunities noticed during inspection — in particular the overlap between
  `activities` and `session_logs`, both of which feed chronic load through duck-typed iteration — are left
  alone. Combining a storage-engine change with that redesign would make any resulting data loss
  impossible to attribute.
