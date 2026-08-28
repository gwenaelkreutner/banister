# Research: Publishing Planned Sessions to the Athlete's Calendar

**Feature**: 005-planned-workout-push | **Date**: 2026-08-28

Phase 0 output. Every finding below was **verified against the live intervals.icu account**
(create → read back → update → re-post → delete → verify cleanup), not read from documentation or
assumed. The probe used a dedicated `banister-selftest:` prefix and deleted everything it created;
cleanup was verified, and the pre-existing foreign event was confirmed untouched.

Write-testing against the real account was explicitly authorized by the athlete before any write.

---

## R1 — The calendar is `/athlete/{id}/events`, and `external_id` is the only usable ownership marker

**Question**: What identifies an entry as ours (FR-013), so republication and withdrawal are safe
(FR-014, FR-015, FR-021)?

**Finding**: `GET/POST/PUT/DELETE /api/v1/athlete/{id}/events`, same basic auth as everything else.
An event carries ~80 fields; the identity-relevant ones:

| Field | Observed | Usable as ownership marker? |
|---|---|---|
| `external_id` | `'cycling-coach:2026-08-27:sweet-spot-2x15'` | **Yes** — free-form, round-trips exactly |
| `oauth_client_id` | `None` | **No** — stays null for personal-key writes, which is the only auth mode this project uses (spec 002) |
| `created_by_id` | `'i000000'` (the athlete) | **No** — identical for the athlete's own writes and ours, since we write *as* them |
| `uid` | server-generated UUID | No — assigned by the server, not chosen by us |

**Decision**: `external_id`, with a `banister:` prefix, is the ownership marker. Format:
`banister:<plan_id>:<yyyy-MM-dd>:<session-slug>` — traceable to the planned session (FR-013) and
scoped to a plan so a regenerated plan's entries are distinguishable from the previous plan's.

**Consequence for FR-015** ("MUST NOT modify or remove entries it did not create"): every read,
update and delete filters on `external_id.startswith("banister:")`. Anything else in the calendar is
invisible to this system by construction, not by care.

## R2 — There is no upsert. Re-POSTing the same `external_id` creates a duplicate

**The single most important finding in this research.**

**Question**: Does the API dedupe on `external_id`, or must idempotence be built client-side
(FR-014, FR-016, FR-017, SC-003)?

**Finding**, verified directly: POSTing an identical payload twice — same `external_id`, same date —
produced **two distinct events** (`id=132274363` and `id=132274365`), both present in the calendar
simultaneously. The API accepted both with `200`.

```
CREATE                     -> 200  id=132274363  external_id='banister-selftest:2026-08-31:probe'
RE-POST same external_id   -> 200  id=132274365  (same as first? False)
events under test prefix   -> 2
```

**Decision**: Idempotence is entirely this system's responsibility. Every publication must:

1. `GET` the events in the target date window,
2. filter to `external_id` starting with our prefix,
3. index by `external_id`,
4. `PUT` the ones that already exist, `POST` only the ones that do not.

**Alternatives considered**: delete-then-recreate the whole window. Rejected — it destroys the
athlete's own edits to our entries (FR-023) with no chance to notice them, and it makes an
interrupted publication (FR-016) leave the calendar emptier than it started, which is worse than
leaving it stale.

**Consequence**: SC-003 ("five consecutive publications produce exactly one entry per session") is a
real, failable test, not a formality — the naive implementation demonstrably fails it.

## R3 — Structured steps are published as a text DSL, parsed server-side

**Question**: How do our `Step`/`RepeatGroup` objects (spec 004) become something a head unit can
execute (FR-008)?

**Finding**: The `description` field accepts a workout DSL, which intervals.icu parses server-side
into a structured `workout_doc` and derives duration and load from. Posting this:

```
Warmup
- 15m 50-65%

Main set
3x
- 12m 88-94%
- 4m 50%

Cooldown
- 15m 50%
```

produced this `workout_doc` (server-generated, we never send it):

```json
{"steps": [
  {"warmup": true, "duration": 900, "power": {"start": 50, "end": 65, "units": "%ftp"}},
  {"reps": 3, "text": "Main set\n3x", "duration": 2880, "steps": [
      {"duration": 720, "power": {"start": 88, "end": 94, "units": "%ftp"}},
      {"duration": 240, "power": {"value": 50, "units": "%ftp"}}]},
  {"cooldown": true, "duration": 900, "power": {"value": 50, "units": "%ftp"}}]}
```

**Decision**: Generate the DSL text, not `workout_doc` JSON. The mapping is near-direct:

| Our model | DSL |
|---|---|
| `Step(kind="warmup")` | a line under a `Warmup` header |
| `Step(kind="work"/"recovery")` | `- {duration}m {lower}-{upper}%` |
| `RepeatGroup(repeat=N)` | `Nx` followed by the inner steps |
| `Step(kind="cooldown")` | a line under a `Cooldown` header |
| `Step.zone_code` | resolved to `%FTP` bounds via `Zone.lower_pct`/`upper_pct` (spec 004 R5) |

**Alternatives considered**: sending `workout_doc` directly. Rejected — the DSL is what the service
documents and parses, the parsed form is derived output rather than input, and generating text keeps
this feature's contract small and inspectable (the athlete sees exactly what they approved, FR-002).

## R4 — The duration model already agrees with intervals.icu's

**Finding, unprompted but load-bearing**: for the probe's `warmup 15 + 3×(12+4) + cooldown 15`
structure, intervals.icu computed `moving_time = 4680` seconds = **78 minutes** — exactly what spec
004's `derive_duration_minutes()` computes for the same shape.

This independently validates the decision made in spec 004 Phase 2 to **include the recovery after
the final repetition** (the old `sets*work + (sets-1)*rest` formula dropped it). intervals.icu
interprets a repeat group the same way this project now does. Had the two disagreed, every published
session's duration would have silently contradicted the plan the athlete approved.

The service also computed `icu_training_load = 78` from the same text — so published entries carry a
load figure the source itself derived, consistent with the metric-authority rule (spec 001 FR-007).

## R5 — A genuine foreign entry already exists, for free

**Finding**: the athlete's calendar contains `external_id: 'cycling-coach:2026-08-27:sweet-spot-2x15'`,
created by **enduragent** — the MIT-licensed reference project this refactor already draws on (its
"never auto-write without explicit approval" pattern is cited in spec 001).

**Consequence**: SC-004 ("verified against a calendar seeded with foreign entries") needs no seeding.
A real third-party entry, written by a different tool using its own `cycling-coach:` prefix, is
already there. Every publication, republication and withdrawal test can assert it survives untouched.

**Decision**: use `banister:` — deliberately distinct from `cycling-coach:` — so the two tools cannot
collide even though they write to the same calendar with the same credential.

## R6 — The client is read-only and must grow write verbs

**Finding**: `app/providers/intervals/client.py` exposes only `_get()`. Spec 002 built it that way
deliberately: intervals.icu was a *source*, and nothing wrote anywhere.

**Decision**: add `_post()`, `_put()`, `_delete()` alongside `_get()`, reusing the same error
classification (401/403 → `CredentialRejectedError`, 429 → `RateLimitedError`, 5xx/network →
`TransientError`), plus `list_events()`, `create_event()`, `update_event()`, `delete_event()`.

**This is the moment the project stops being read-only.** Spec 001's FR-019 ("MUST NOT write anything
into the athlete's account without explicit prior approval for the specific content") stops being
hypothetical here — every one of these verbs is a path that must be gated on a recorded approval.

---

## Open questions carried into implementation

1. **Rate-limit behaviour under a full-horizon publish** (FR-027, SC-010). Publishing two weeks is
   on the order of 10 requests; the documented quotas (spec 002 research R4: ~5000/day, 2500/15min)
   leave enormous margin. Deliberately not stress-tested — deliberately exhausting the athlete's
   quota to observe the failure is not worth doing, same call spec 002 made for its own rate-limit
   question.
2. **What the calendar rejects as unrepresentable** (FR-028). The probe's structures were all
   accepted. A `push_errors` field exists on the event model, which suggests the service reports
   per-event push problems — worth reading on a real published entry once sessions with unusual
   shapes (a 1-minute Z6 sprint step, spec 004's recovery-week session) are published for real.
3. **Athlete-edit detection** (FR-023, US5). ~~The event carries an `updated` timestamp. Whether it
   changes only on athlete edits or also on our own writes decides how divergence is detected.~~
   **Resolved in Phase 7 by not depending on it.** `detect_athlete_edit()` /
   `calendar.remote_event_hash()` compare the remote event's *content* (date | name | description)
   against the stored `PublishedEntry.content_hash` — a mismatch means the remote event is neither
   what we last wrote nor what we would write now, i.e. an out-of-band edit. This sidesteps the
   `updated`-timestamp ambiguity entirely.
   **Probe done (T054, live account, 2026-08-28)**: intervals.icu echoes `description` back
   **byte-for-byte** — a session published then re-read has an identical `description`, and five
   consecutive republications all report "unchanged" with zero writes. The content-based detection
   is sound. Confirmed alongside: `workout_doc` is derived server-side (3 steps for the probe's
   warmup/3×/cooldown shape), and `moving_time` matched the plan's `derive_duration_minutes()`
   exactly (78 min, then 72 min after a step-length change) — R4 holds on real data.
