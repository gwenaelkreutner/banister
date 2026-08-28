# Contract: Calendar Publication

**Feature**: 005-planned-workout-push | **Date**: 2026-08-28

Two interfaces cross a boundary here: the **workout DSL** this system writes into intervals.icu (a format
contract with an external service), and the **approval request** the athlete sees before anything is
written (a contract with a person, and the one FR-002 is specific about).

Every format detail below was verified against the live API, not read from documentation
([research.md](./research.md) R3).

---

## 1. The workout DSL

Written to the event's `description` field. intervals.icu parses it server-side into `workout_doc` and
derives duration and training load from it — we never send those.

### Shape

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

### Mapping from spec 004's session model

| Our model | Rendered as |
|---|---|
| `Step(kind="warmup")` | a line under a `Warmup` header |
| `Step(kind="work")` / `Step(kind="recovery")` | `- {duration}m {lower}-{upper}%` under `Main set` |
| `Step(kind="cooldown")` | a line under a `Cooldown` header |
| `Step(kind="steady")` | a single line, no section headers — an unstructured ride |
| `RepeatGroup(repeat=N)` | `Nx` on its own line, then the inner steps |
| `Step.zone_code` | `%FTP` bounds from `Zone.lower_pct`/`upper_pct` × 100 |

### Rules

- **Intensity is always `%ftp`.** Steps store zone codes, never absolutes (spec 004) — the percentage
  bounds come from the plan's own `zones`, so a threshold change retargets published sessions the same
  way it retargets displayed ones.
- **Durations are whole minutes** (`m` suffix), matching `Step.duration_minutes`.
- **A repeat group renders its full inner unit**, including the recovery after the final repetition.
  This is not an arbitrary choice: intervals.icu computed exactly the same total duration for this shape
  as spec 004's `derive_duration_minutes()` does (research R4). The two systems agree.
- **A session with no steps is never rendered.** It is refused and reported (FR-009) — the renderer
  raises rather than emitting an empty or invented workout.

### Verified server-side result

Posting the shape above produced this `workout_doc` — recorded here as the reference fixture the
renderer's tests assert against:

```json
{"steps": [
  {"warmup": true, "duration": 900, "power": {"start": 50, "end": 65, "units": "%ftp"}},
  {"reps": 3, "text": "Main set\n3x", "duration": 2880, "steps": [
      {"duration": 720, "power": {"start": 88, "end": 94, "units": "%ftp"}},
      {"duration": 240, "power": {"value": 50, "units": "%ftp"}}]},
  {"cooldown": true, "duration": 900, "power": {"value": 50, "units": "%ftp"}}]}
```

with `moving_time: 4680` (78 min) and `icu_training_load: 78`, both server-derived.

---

## 2. The event payload

| Field | Value | Why |
|---|---|---|
| `start_date_local` | `{yyyy-MM-dd}T00:00:00` | the planned date |
| `category` | `"WORKOUT"` | what makes it a planned session rather than a note |
| `type` | `"Ride"` | the sport |
| `name` | the session's rendered description | what the athlete sees in their calendar |
| `external_id` | `banister:<plan>:<date>:<slug>` | ownership marker — see data-model.md |
| `description` | the DSL above | parsed into executable steps |

**Never sent**: `workout_doc` (derived), `moving_time` / `icu_training_load` (derived), `id` / `uid`
(server-assigned).

---

## 3. The approval request

FR-002 requires the athlete to see *which sessions, on which dates* before deciding. The contract:

```
📤 Publier vers ton calendrier intervals.icu

Période : lun 1 sept → dim 14 sept (2 semaines)

  mar 02/09  Intervalles 3×12min Z4        78 min
  jeu 04/09  Endurance Z2                  90 min
  sam 06/09  Sortie longue Z2             180 min
  ...

7 séances au total.

⚠️ Pour que ces séances arrivent sur ta montre, le transfert vers ton
appareil doit être activé dans TES réglages intervals.icu — je ne peux
pas le faire à ta place. [comment faire]

                    [ ✅ Publier ]  [ ❌ Annuler ]
```

### Rules

- **Every session that will be written is listed**, with its date. Not a count, not a summary — FR-002
  says "which sessions, on which dates".
- **The horizon is stated** (FR-010), not left implicit.
- **The device-forwarding caveat appears before the first publication** (FR-012, SC-009). Without it the
  first publication looks like it silently did nothing, and the feature appears broken.
- **Declining writes nothing and does not re-ask** (FR-003).
- **The approval is bound to this exact content** via `content_hash` (FR-004). If the plan changes before
  the athlete taps approve, the write is refused and fresh approval requested — checked at publication
  time, not only when the request was built.

### After publication (FR-006)

What was *actually* written is reported, including failures — never a blanket "done":

```
✅ 6 séances publiées, 1 échec

  ✅ mar 02/09  Intervalles 3×12min Z4
  ...
  ❌ dim 07/09  Récupération Z1 — refusée par le calendrier (structure non représentable)
```

A per-session failure never abandons the rest of the publication (FR-028).

---

## 4. Guarantees

| Guarantee | Requirement |
|---|---|
| No write occurs without a stored `PublicationApproval` in `approved` state whose `content_hash` matches what is about to be written | FR-001, FR-004, FR-005 |
| Only events whose `external_id` starts with `banister:` are ever read-for-diff, updated, or deleted | FR-015 |
| Republishing a period updates existing entries — never duplicates, because the API does **not** upsert (research R2) | FR-014, FR-017, SC-003 |
| An interrupted publication resumes from stored `PublishedEntry` rows without re-creating what succeeded | FR-016, SC-007 |
| Sessions dated in the past are never written or rewritten | FR-011, FR-022 |
| A session without steps is refused and reported, never fabricated | FR-009 |
| An athlete-edited or athlete-deleted entry is surfaced for a decision, never silently overwritten or recreated | FR-023, FR-024 |
| Withdrawal removes every `banister:`-prefixed entry and nothing else | FR-021, SC-006 |
