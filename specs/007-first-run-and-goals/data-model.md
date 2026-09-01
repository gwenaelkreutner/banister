# Data Model: First Run, Goals, and Coach Voice

**Feature**: 007-first-run-and-goals | **Date**: 2026-09-01

Entities from [spec.md](./spec.md) §Key Entities, resolved against what the live source supplies and what
the codebase already persists ([research.md](./research.md)).

**This feature is light on new storage.** Most of it is *flow* — reading the source, confirming, asking
less. The only persisted additions are two nullable columns on `users` and one on `athlete_profiles`. No
new table.

---

## Not stored: the Read Profile

The set of attributes read from `GET /athlete/{id}` at the start of setup — assembled on demand, shown for
confirmation, never persisted as its own thing (it becomes the `athlete_profiles.profile` JSON once
confirmed).

| Attribute | Source field | Origin shown to athlete (FR-005) | Age shown (FR-005) |
|---|---|---|---|
| sport | `sportSettings[].types` | "lu depuis intervals.icu" | — |
| FTP | `sportSettings[cycling].ftp` | "lu depuis intervals.icu · Réglages sport" | intervals.icu does not date this |
| LTHR | `sportSettings[cycling].lthr` | idem | idem |
| max HR | `sportSettings[cycling].max_hr` | idem | idem |
| resting HR | `icu_resting_hr` | "lu depuis intervals.icu" | — (⚠️ profile default, not the measured series — spec 006 R1) |
| weight | `icu_weight` | "lu depuis intervals.icu" | — |
| sex | `sex` | "lu depuis intervals.icu" | — |
| date of birth / age | `icu_date_of_birth` | "lu depuis intervals.icu" | exact |
| current fitness (CTL/ATL/TSB) | `wellness` table | "calculé par intervals.icu, à jour au {date}" | dated |
| recent actual volume | last ~6 weeks of `activities` | "d'après tes sorties des 6 dernières semaines" | dated |
| locale (voice default hint) | `locale` | not shown — used to pick the default persona | — |

**FR-009 rule**: if `sportSettings` has no cycling entry with an FTP, the reader returns FTP as *absent*,
setup proceeds in HR mode on LTHR/max_hr, and states "je n'ai pas de FTP mesurée pour toi — je pilote sur
la fréquence cardiaque".

**FR-010 rule**: if `wellness` has < ~14 days and `activities` < ~4 weeks, the initial CTL is seeded with
`estimate_initial_ctl(tss_from_weekly_hours(intended_hours))` and setup says "je démarre sur une
estimation prudente de ta forme — elle se calera sur tes données réelles en quelques semaines".

## Not stored: Athlete-Only Inputs

Gathered in the confirmation flow, folded straight into `athlete_profiles.profile`:

| Input | Goes to | Validation |
|---|---|---|
| goal type | `profile.objective.type` | one of the existing enum values |
| goal date | `profile.objective.target_date` | **not in the past**; challenged if < 21 days or > 365 days out (FR-015) |
| intended weekly hours | `profile.availability.hours_per_week` | > 0; if it disagrees sharply with actual recent volume, the flow *notes* the gap, does not block (spec edge case) |
| health constraints | `profile.health_constraints` / `profile.injury_status` | unchanged from today's `/setup` |

## Persisted: three nullable columns, one Alembic revision

### `users.coach_voice` — `String(64) | None`

The persona id the athlete selected. `None` ⇒ use `settings.persona` (the deployer default). Read per
LLM request; `load_persona()` is `lru_cache`d on the file so a change takes effect on the next message with
no restart (FR-023). If the id does not resolve, fall back to `coach-default` and say so (FR-026).

**Not on `athlete_profiles`**: voice is not a training input and must survive `/reset` differently — see
below. It is identity state, so it lives on `users`.

### `users.disclaimer_acknowledged_at` — `UtcDateTime | None`

Set the first time the disclaimer is shown. The disclaimer is sent from `_finalize_setup` / the `/goal`
flow **only when this is null** (FR-028, US6 acceptance 2). Gives spec 007's own first-run flow a clean
hook and stops `/setup` re-sending it.

### `athlete_profiles.profile` additions (JSON, no migration)

The `profile` JSON gains provenance markers so FR-005 / SC-003 / SC-004 are answerable after the fact:

```
profile.equipment.ftp_source  : "declared" | "estimated" | "source"   (new value: "source")
profile.physio.hr_max_source  : idem
profile.physio.hr_rest_source : idem
profile.read_from_source_at   : ISO datetime — when the confirmation flow last read the source
```

`"source"` replaces the current silent `"estimated"` when a value came from `sportSettings` / `icu_*`.
This is the Constitution IV provenance rule applied to the new read path.

## State transitions

### Setup / goal flow (FSM)

```
FIRST RUN (no active plan):
  /setup → read source → CONFIRM_PROFILE → [athlete edits a value → CORRECT_VALUE → back to CONFIRM]
         → ASK_GOAL → ASK_DATE → ASK_VOLUME → ASK_CONSTRAINTS → generate → disclaimer (if unack'd) → ACTIVE

GOAL CHANGE (active plan exists):
  /goal  → read source silently → ASK_GOAL → ASK_DATE → [ASK_VOLUME only if athlete asks to change it]
         → regenerate from current fitness → check_divergence (spec 005) → "what changed" summary → ACTIVE
```

`CONFIRM_PROFILE` / `CORRECT_VALUE` are new `SetupStates`. The goal-change path reuses `ASK_GOAL` /
`ASK_DATE`.

### `/reset`

```
/reset → list what will be discarded (N sessions, M messages, adherence, active plan, profile)
       → require typed confirmation ("SUPPRIMER")
       → declined / anything else typed → discard NOTHING (FR-019)
       → confirmed → delete via the User cascade relationships; coach_voice and
         disclaimer_acknowledged_at are KEPT (identity, not training data);
         intervals.icu untouched (FR-020, SC-007)
```

The `User` model already has `cascade="all, delete-orphan"` on `session_logs`, `chat_messages`,
`activities`. `training_plans`, `athlete_profiles`, `weekly_adherence`, publication tables cascade on the
FK. So `/reset` deletes the child rows explicitly (or deletes+recreates the `User`), keeping only
`telegram_id`, `first_name`, `coach_voice`, `disclaimer_acknowledged_at`, and reminder prefs.

## What this feature does not model

Plan generation (`generate_plan`) is unchanged (spec Scope, and Assumptions: "the current implementation
is the behavioural baseline"). `_MODE_PERSONA` / narrative modes are unchanged and not merged with voices.
The `personas/*.yaml` format is unchanged except that `ux_prompt` stops being required (research R2) — a
persona is one `system_prompt`.
