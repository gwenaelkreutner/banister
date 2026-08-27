# Phase 1 Data Model: intervals.icu as Sole Training Data Source

**Feature**: 002-intervals-icu-provider | **Date**: 2026-08-27

Three things change: two tables are added, one is removed, and the *provenance* of several existing
columns changes even though the columns themselves do not.

---

## The field mapping — where each value comes from after this change

This is the heart of the feature. Every row is a value `session_logs` already stores; what changes is who
computes it.

### Consumed from the source, never recomputed (FR-015, FR-016)

| Column | intervals.icu field | Note |
|---|---|---|
| `tss_actual` | `icu_training_load` | ⚠️ null-vs-zero convention must be confirmed (research R3) |
| `intensity_factor` | `icu_intensity` | Currently computed as NP/FTP; stops being computed |
| `avg_power` | activity summary | |
| `normalized_power` | activity summary | Currently has a `normalized_power_source` discriminator ("strava" \| "computed") — becomes single-source, so the discriminator loses its purpose |
| `avg_heart_rate` | activity summary | |
| `kilojoules` | activity summary | |
| `elevation_gain_m`, `average_temp_c`, `athlete_count` | activity summary | Context fields, unchanged in meaning |

Fitness state (`ctl_at_session`, `atl_at_session`, `tsb_at_session`) currently comes from the local
`atl_ctl` engine. The source provides `icu_ctl` / `icu_atl` per activity and per wellness day, so these
become consumed too — with the consequence that the athlete's fitness numbers finally agree with what they
see on intervals.icu, which is the entire point of spec 001's metric-authority rule.

### Retained as local computation (FR-018, plus research R5)

| Column | Why the source cannot provide it |
|---|---|
| `respect_zones_score` | Compares actual zone time against the **planned** zone — the source does not know our plan |
| `cardiac_drift_index` | Our own formula over the heart-rate series |
| `intervals_consistency_index` | Our own formula over the power series |
| `session_type_real` | Our own classification (`intervals` / `tempo` / `long_ride` / …) |
| `variability_index` | Trivially derived from two consumed values (NP / avg power) |
| `dominant_zone` | Derived from zone times |
| `kpi_contribution` | Adherence scoring — entirely ours |

⚠️ **These six are the open question in [plan.md](./plan.md)**: they appear in neither FR-015's consume
list nor FR-018's retain list. The recommendation is to retain them.

### Ceasing to exist

| Column | Fate |
|---|---|
| `strava_activity_id` | Replaced by the source's activity id — same role, different provider |
| `source` (`"manual"` \| `"strava"`) | Only one source remains; the column becomes vestigial |
| `normalized_power_source` | Was a provenance discriminator between Strava's value and ours; with a single source there is nothing to discriminate |

**Note on removing provenance columns**: Principle IV requires provenance to be explicit, so removing a
provenance discriminator deserves scrutiny rather than being waved through. It is safe here precisely
*because* there is now exactly one possible origin — a discriminator with one possible value records
nothing. If a second provider is ever added, it must come back.

---

## New: `wellness`

Dated recovery signals, one row per day (FR-023, FR-024). Captured but not interpreted — readiness
thresholds belong to spec 006.

| Field | Source field | Notes |
|---|---|---|
| `date` | wellness record `id` | ISO local date, the natural key with `user_id` |
| `hrv` | `hrv` | **Nullable — a missing reading is unknown, never zero** (FR-024) |
| `resting_hr` | `restingHR` | Same |
| `sleep_seconds` | `sleepSecs` | Same |
| `weight_kg` | `weight` | Same |
| `ctl`, `atl` | `ctl`, `atl` | Daily fitness state as the source computes it |

**The nullability is the requirement, not a detail.** A missing HRV reading stored as `0` would later read
as a catastrophic drop once spec 006 evaluates thresholds against it — the exact failure FR-024 exists to
prevent, and one that would surface as a wrong training recommendation rather than an error.

---

## New: sync state

Two concerns, both about not losing track. Whether they are one table or two is an implementation choice;
the requirements are separate.

### Reported markers (FR-009, FR-010, FR-012)

The durable record that an activity has already been announced to the athlete.

- Keyed by the source's activity id — stable across edits, which is exactly why FR-010's "an edited
  activity is not a new activity" comes for free
- **Written only after delivery succeeds** (FR-012): marking before sending would lose a notification
  whenever the messaging platform is briefly unreachable
- Must survive restarts (FR-009) — which is what makes this a table rather than in-memory state

### Import and refresh progress (FR-021, FR-027, FR-028)

- Timestamp of the last successful refresh — feeds FR-021's "report the age of the data you are using"
- How far back history has been imported — lets an interrupted import resume without duplicating (FR-027)
  and lets the coach say history is still loading rather than presenting partial figures as complete
  (FR-028)

---

## Removed: `oauth_connections`

Spec 003 deliberately kept this table intact, because the provider it authorized was still in use. That is
no longer true: this feature removes that provider, so the table becomes genuinely obsolete and is dropped
here (FR-036).

The athlete's intervals.icu key does **not** take its place in this table — it is configuration, held
encrypted (FR-004), not a per-connection database row. The delegated-authorization model this table
existed to support is gone entirely, not replaced.

---

## `activities` — an open question

Populated today by the Strava historical import. It is re-fetchable from the new source, so it could be
truncated and repopulated, or migrated in place, or left alone. Spec 003 deferred this deliberately and
this feature must decide it — see [plan.md](./plan.md) Open Questions.

Worth noting: `compute_fitness_from_any()` accepts a mixed list of `SessionLog` and `Activity` by
duck-typing. Whatever is decided must not break that, or must remove the need for it.

---

## What explicitly does not change

Recorded so a reviewer can check the diff against it:

- `training_plans`, `athlete_profiles`, `weekly_adherence`, `chat_messages` — untouched
- The `session_logs` ↔ `training_plans` relationship and its cascade
- `plan_technical`'s document shape (spec 004 changes that, not this one)
- The activity-to-session matching score and window (FR-033)
