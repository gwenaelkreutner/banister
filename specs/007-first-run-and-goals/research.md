# Research: First Run, Goals, and Coach Voice

**Feature**: 007-first-run-and-goals | **Date**: 2026-09-01

Phase 0 output. Every finding was checked against **the live intervals.icu account and the running
codebase**, not assumed. Two of them change the shape of the work.

---

## R1 — The source supplies every setup question except goal, date, and intended volume

**Question**: The spec's thesis is "read, don't ask". Does `GET /athlete/{id}` actually contain what the
six-question setup asks for?

**Finding**, from a live `get_athlete()` probe on this account:

| Setup asks | Source field | This athlete |
|---|---|---|
| Sport | `sportSettings[].types` | `["Ride", "VirtualRide", …]` → cycling |
| Power meter in use? | `sportSettings[Ride].ftp` present | `290` → yes |
| FTP | `sportSettings[Ride].ftp` | **290** |
| — (LTHR, for HR mode) | `sportSettings[Ride].lthr` | **182** |
| Age → HR max | `icu_date_of_birth` **and** `sportSettings[Ride].max_hr` | DOB `1990-01-01`; max_hr **202** |
| — (weight) | `icu_weight` | **67.0** |
| — (sex) | `sex` | `M` |
| — (resting HR) | `icu_resting_hr` | `65` (profile default — the *measured* RHR series ended 2026-07-19, see spec 006 R1) |
| — (locale, for voice default) | `locale` | `fr` |
| Goal type | — | not in the source |
| Goal date | — | not in the source |
| Intended weekly volume | — | the source shows *actual* volume (recent activities); "what you intend" ≠ "what you've done" (spec Assumptions) |
| Health constraints | — | not in any data source |

**Important**: the threshold figures live in `sportSettings[]` **per sport**, not the top-level `icu_ftp`
(which is `null` on this account). The reader must pick the cycling entry from `sportSettings` by matching
`types`.

**Decision**: setup reads all of the above, shows it for confirmation (FR-002), and asks only: goal + its
date, intended weekly volume, and health constraints (FR-003). Age is no longer asked — `max_hr` comes
straight from the source, and `icu_date_of_birth` gives an exact age if one is still wanted for anything.
`hr_rest` comes from `icu_resting_hr` rather than the current hard-coded `60`.

**Consequence for SC-001 / SC-002**: with this account, setup is genuinely one confirmation screen + three
questions (goal, date, volume) + the constraints question. SC-002 ("zero readable attributes asked for") is
achievable and testable.

## R2 — `load_persona()` works; it is simply never called. `personas/pace.yaml` is already the French voice

**Question**: The spec's "known starting point" says the voice-loading mechanism "already exists and
works". Does it?

**Finding** — *corrected after a direct test* (an earlier draft of this file claimed the loader was broken;
it is not — that was a misread of truncated output, and the spec's "code is the authority" rule applies):

`load_persona()` loads **all four** shipped personas without error. Each `personas/*.yaml` has both
`system_prompt` and `ux_prompt`. `personas/pace.yaml` is **already a complete French "Pace" voice**
(`language: fr`, 2160-char system prompt, 1492-char ux prompt) that closely matches the live inline text.

So the mechanism is genuinely finished. The only missing piece is the wiring: `prompts.py` holds ~7 inline
prompts instead of calling `load_persona()`, under two identities ("Pace" in the chat/UX path, "Banister"
in the plan/recap/blocks paths):

| Constant / function | Identity | Consumed by |
|---|---|---|
| `COACH_SOUL` | Pace | `tools.py::build_system_prompt` (chat) |
| `build_ux_system_prompt()` | Pace | `chat.py`, `forme.py`, `activity_analysis.py` |
| `PLAN_SYSTEM_PROMPT`, `WEEK_SYSTEM_PROMPT` | Banister | `narrator.py` |
| `WEEKLY_RECAP_SYSTEM_PROMPT` | Banister | `weekly_recap.py` |
| `COACH_BLOCKS_SYSTEM_PROMPT` | Banister | `activity_analysis.py` (blocks mode) |
| `build_narrative_system_prompt()` + `_MODE_PERSONA` | — (narrative modes) | `activity_analysis.py` (narrative mode) |

**Decision**: "finishing an existing unfinished piece" means, concretely:
1. **No schema change** — the `Persona` dataclass and `load_persona()` are fine as they are. `system_prompt`
   is the coach identity, `ux_prompt` is the response-style layer; the split maps cleanly onto
   `build_system_prompt`'s `COACH_SOUL` vs `build_ux_system_prompt`'s rules.
2. **`personas/pace.yaml` becomes the default** (`settings.persona` → `"pace"`), since it already matches
   the live voice and is French. `coach-default.yaml` / `analyst.yaml` / `zen.yaml` are English — rewrite
   at least one into a genuinely different **French** voice so `/voice` has a real choice, and keep a
   `coach-default` that always resolves (the FR-026 fallback target).
3. Wire `load_persona()` into the **chat path first** (`build_ux_system_prompt` + `COACH_SOUL`), which is
   where the athlete actually converses. The narrator/recap/blocks paths (identity "Banister") are
   lower-traffic and can move in the same change or a follow-up — but they must end up on the *same*
   selected voice, not a second hard-coded identity.
4. `_MODE_PERSONA` (narrative modes) **stays** and is not merged (spec Assumptions, and confirmed here —
   it is a different axis: *how one ride is recounted*, not *who the coach is*).

## R3 — Writing a correction back to the source: probe deferred, FR-007 is the shipping default

**Question**: FR-006 requires a corrected FTP / weight to be written back to intervals.icu. Is there a
write endpoint, and should we use it?

**Finding**: the events API accepts `PUT` (spec 005 proved it), and the athlete/sportSettings objects are
almost certainly `PUT`-able too, but **this was not probed** — a failed or malformed write to an athlete's
account *settings* (not a calendar event that can be deleted) is a different risk class, and the probe
needs its own explicit authorisation.

**Decision**: ship FR-007 as the default path, and treat write-back as an opt-in enhancement:
- A correction the athlete approves is **first** attempted as a write-back **only if** a verified,
  reversible endpoint is confirmed during implementation (its own probe, its own go/no-go).
- Until then, and whenever write-back fails or is refused (FR-006c): the athlete is told *"change this at
  intervals.icu — Settings → Sport Settings → FTP; I read from there and won't keep a different number"*,
  and the coach does **not** persist a divergent local value (FR-007). Setup either waits for the source
  to reflect the change (re-read on next `/setup` or a manual "re-check") or proceeds on the source's
  current value with the divergence stated.

**Rationale**: this preserves the single-source-of-truth guarantee (Constitution IV) with zero risk to the
athlete's account, and the write-back — the genuinely nice-to-have part — can be added once its endpoint is
verified the same way spec 005 verified the events API.

**Alternative considered**: local override with a `*_source: "corrected"` marker. Rejected — it is exactly
the coach-vs-log divergence the metric-authority rule exists to prevent, and the spec's own Assumptions
call it out.

### R3 addendum (2026-09-07) — read-only endpoint shape verified

Step (a) of the probe (read only, no write) is done. Findings from the published OpenAPI spec
(`https://intervals.icu/api/v1/docs`, the source RapiDoc renders) and live `GET` calls on this account:

- **Read endpoints confirmed live**:
  - `GET /api/v1/athlete/{id}/sport-settings` → array of per-sport entries.
  - `GET /api/v1/athlete/{id}/sport-settings/{id}` where `{id}` is the numeric settings id **or an
    activity-type alias** (`Ride`, `Run`, …). `…/sport-settings/Ride` returns the cycling entry directly —
    no need to discover the numeric id (`2336680` on this account).
- **Write endpoint documented** (not yet exercised):
  `PUT /api/v1/athlete/{athleteId}/sport-settings/{id}` — summary *"Update sport settings by id or activity
  type"*. Request body: `application/json`, schema `SportSettings` (62 props; `ftp`, `indoor_ftp`, `lthr`,
  `max_hr` are `int32`). One **required** query param: `recalcHrZones` (boolean). Only a `200` response is
  documented.
- Sibling endpoints for context: `PUT …/sport-settings` (bulk), `POST …/sport-settings` (create with
  defaults), `PUT …/sport-settings/{id}/apply` (re-derive zones on matching activities, async).
- The cycling entry on this account: settings id `2336680`, `ftp: 290`, `lthr: 182`, `max_hr: 202`,
  `eFTPSupported: true`. Athlete id resolves as `i000000` (our client passes `"0"`, which the API accepts).

**Open questions that only a real `PUT` (step b) can settle**:
1. Does the API merge a **partial** body (`{"ftp": 300}`) or require the full `SportSettings` object?
   The `events` API tolerated partials; unverified here.
2. What does `recalcHrZones` do to power zones — is it a no-op for an FTP-only change, or does it need a
   companion `recalcPowerZones`-style flag that isn't in the spec?
3. Is the change reversible with a second `PUT` back to the old value with no side effects (zone history,
   `updated` timestamps, activity re-processing via the `/apply` path)?
4. Auth scope: does a personal API key (basic auth) have write permission on settings, or is this
   OAuth-scope gated? (`403` would answer immediately.)

**Recommended step (b) shape when authorised**: single `PUT …/sport-settings/Ride?recalcHrZones=false`
with a **partial** `{"ftp": <same value it already has>}` (a no-op write) to answer Q1 + Q4 with zero
real change, then one real round-trip (set to a test value, read back, restore) to answer Q2 + Q3.

## R4 — "Change the goal" vs "start over": most of the preservation is already true; the gap is the flow

**Question**: The spec says changing a goal today "is served by the same action as starting over, which
discards context". Is that accurate?

**Finding**, from the code: `/setup` → `_finalize_setup` does **not** delete anything.
`plan_repo.deactivate_all_for_user` sets `status="inactive"` (no delete); `session_logs`, `chat_messages`,
`weekly_adherence`, `activities` are keyed on `user_id` and survive; `profile_repo.update` overwrites the
profile row in place. `_finalize_setup` already reads `get_current_fitness()` so a rebuilt plan does start
from current CTL/ATL/TSB, not zero.

So **FR-011/FR-012 are largely already satisfied by accident**. The real gaps are behavioural:
- `/setup` re-asks all six questions every time (FR-013 violation — it re-asks unchanged info).
- There is no *distinct* "start over" that actually discards (`session_logs` etc. are never cleared, so a
  genuine clean slate is impossible today — FR-017/FR-018/FR-019 have nothing to hook into).
- Nothing surfaces that calendar entries published under the old plan are now stale (FR-014) — though spec
  005's `check_divergence` already exists and just needs to be triggered on a goal change.

**Decision**:
- **`/goal`** (new, or a branch of `/setup`): re-reads the source profile silently, asks only goal + date
  (+ volume if the athlete wants to change it), keeps the same profile row, regenerates the plan from
  current fitness, then runs `check_divergence` and tells the athlete if the calendar is now stale
  (FR-014). Shows a "what changed / what carried over" summary (FR-016).
- **`/reset`** (new): lists exactly what will be deleted (N logged sessions, M chat messages, adherence
  history, the active plan, the profile), requires a typed confirmation, then deletes — the `cascade`
  relationships on `User` already make this a single set of deletes. Never touches intervals.icu (FR-020,
  SC-007) — it only ever deletes local rows.
- Goal-date sanity (FR-015): reject a past date; challenge a date < ~3 weeks out ("too soon for a
  meaningful build") or > ~1 year ("that far out the plan is mostly guesswork — pick a nearer checkpoint").

## R5 — Coach voice must become athlete state; one nullable column

**Finding**: `settings.persona` (default `"coach-default"`) is deployer configuration only. FR-021 (change
it any time) and FR-024 (athlete state, config as default) require it to move.

**Decision**: `users.coach_voice: str | None` (nullable, `None` = use `settings.persona`). One Alembic
revision. A `/voice` command lists the available personas with their `voice` descriptor (FR-022), sets the
column, and the next LLM call picks it up (FR-023 — no restart needed because `load_persona` is
`lru_cache`d on the *file*, and the column read happens per request). FR-026: if the stored or configured
persona id does not resolve, fall back to a guaranteed-present `coach-default` and prepend one line to the
response saying so.

`personas/README.md` already documents the file format; adding a voice is a YAML file, no code change —
which is the open-source point (spec Rationale).

## R6 — The disclaimer is built; it needs a "shown once" flag

**Finding**: spec 006 T054 already emits `prompts.DISCLAIMER_TEXT` at the end of `_finalize_setup` and put
it in the README. FR-027 is satisfied for a first run. **FR-028** ("not repeated at every interaction") is
also satisfied — it is only sent from `_finalize_setup`, not from the chat path — but re-running `/setup`
re-sends it, and there is no record that it was acknowledged.

**Decision**: add `users.disclaimer_acknowledged_at: datetime | None`. `_finalize_setup` (and the new
`/goal` flow) sends the disclaimer **only if** that column is null, then sets it. This also gives spec
007's own first-run flow a clean hook, and satisfies US6 acceptance 2 precisely.

---

## Open questions carried into implementation

1. **The sportSettings write-back endpoint** (R3). Needs its own probe with explicit authorisation before
   any write-back code is written. Until verified, FR-007 is the only path and it is sufficient.
2. **Which sport entry to read when the athlete has multiple** (R1). This account's `sportSettings` has
   four entries; the cycling one is identified by `types` containing `"Ride"`. An athlete who only runs
   would have no cycling FTP — that is the FR-009 case ("no measured threshold → proceed on what they
   have, state the basis"). Decide the exact matching rule when writing the reader.
3. **Whether `/goal` is a new command or a mode of `/setup`** (R4). Leaning new command — `/setup` keeps
   its "I want to reconfigure everything" meaning, `/goal` is the light path — but the FSM wiring decides
   this and it is a 5-minute call during Phase 1 design of tasks.
