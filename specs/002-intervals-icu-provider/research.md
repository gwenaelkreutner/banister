# Phase 0 Research: intervals.icu as Sole Training Data Source

**Feature**: 002-intervals-icu-provider | **Date**: 2026-08-27

Findings below come from intervals.icu's own documentation and forum, **and — since R9 — from calls against
the author's live account** (54 real activities, 8 wellness days, real streams). Sections R1–R8 were
written before a key was available and preserved their unknowns honestly; **R9 supersedes several of them
with measured fact.** Where the two disagree, R9 wins.

---

## R1. Authentication: a personal key over basic auth

**Decision**: Basic authentication with the literal username `API_KEY` and the athlete's personal key as
the password.

**Evidence** (quoted from the official API thread):

> "The username is 'API_KEY' and the password your API key."

```
curl -u API_KEY:<key> https://intervals.icu/api/v1/athlete/2049151/activities
```

**Also confirmed**: `0` may be used in place of the athlete id — "This will use the athlete for the API key
or bearer token used to make the call." This removes the need to make the athlete id a required setting,
though keeping it configurable is still useful for verification (R2).

**Why this matters here**: FR-001 requires no delegated authorization flow, no registered application, no
inbound callback. Basic auth with a personal key satisfies all three, and is the single largest reason the
self-hosted story becomes credible (spec 001).

**Alternatives rejected**: OAuth — available, but requires registering an application, which FR-001
explicitly forbids and which spec 001 rejected for exactly this reason.

---

## R2. Endpoints

**Decision**: Three endpoints cover everything this feature needs.

| Purpose | Path | Parameters |
|---|---|---|
| Verify credential, identify athlete | `GET /api/v1/athlete/{id}` | — |
| List activities | `GET /api/v1/athlete/{id}/activities` | `oldest`, `newest` (`yyyy-MM-dd`) |
| Wellness records | `GET /api/v1/athlete/{id}/wellness` | `oldest`, `newest`, optional `fields` |

**Why this matters here**: FR-002 (verify at startup, identify the athlete) maps directly onto the athlete
endpoint. FR-006's periodic detection maps onto the activities endpoint with a moving date window rather
than a "since id" cursor — the API filters by date, not by sequence.

**⚠️ To confirm with a real key**: whether the activities endpoint accepts a plain-JSON response by default
(the documented example uses `activities.csv`), and whether an unbounded request without `oldest`/`newest`
is permitted or errors.

---

## R3. Field mapping — what the source already computes

**Decision**: Consume these unmodified (FR-015, FR-016 forbids recomputing them).

| Our concept | intervals.icu field |
|---|---|
| Training load (TSS) | `icu_training_load` |
| Intensity factor | `icu_intensity` |
| Chronic training load (CTL) | `icu_ctl` |
| Acute training load (ATL) | `icu_atl` |
| Functional threshold power | `icu_ftp` |
| Detected intervals | `icu_intervals` |

Wellness records carry `hrv`, `restingHR`, `sleepSecs`, and also `ctl`, `atl`, `rampRate`, `weight` — so
fitness/fatigue are available per-day from the wellness endpoint as well as per-activity.

**Why this matters here**: this is the concrete form of spec 001's metric-authority rule. Every value in
this table is one the local engine currently computes and must stop computing (FR-017).

**⚠️ To confirm with a real key**: whether `icu_training_load` is `null` or absent when the source has not
finished analysing an activity. FR-020 requires "unknown" to stay distinguishable from zero, and the whole
correctness of chronic-load accumulation depends on getting this right — a not-yet-computed load stored as
`0.0` reads as a rest day. This is the single most consequential unknown in this research.

---

## R4. Detecting new activities by polling a date window

**Decision**: Poll `GET /activities` with `oldest` set a few days back and `newest` set to today, then
compare returned activity ids against a locally recorded set of already-reported ids.

**Rationale**: The API filters by date, not by "everything since id X". A moving window of a few days is
therefore the natural query, and deduplication has to happen locally regardless. Recording reported ids
locally also satisfies FR-009's requirement that exactly-once notification survive restarts, and FR-010's
requirement that an edited activity not be re-reported — an edit does not change the activity's id.

**Quota headroom** (published: 5,000 requests/day, 2,500 per rolling 15 minutes, 10/second): at the
five-minute interval FR-007 mandates, steady-state polling is 288 requests/day, under 6% of the daily
allowance. Even with a detail fetch per new activity, headroom is large (FR-008).

**Alternatives rejected**: webhooks — evaluated and rejected in spec 001 (they require registering an
application, are documented as not firing for Strava-sourced activities, and add their own consolidation
delay). Not reconsidered here.

---

## R5. The quality metrics — a gap in the specification ⚠️

**This is the most important finding, and it is a question rather than a decision.**

`AnalyzedSession` carries six values that are neither in FR-015's "consume from the source" list nor in
FR-018's "retain locally" list:

| Value | Computed from | Does intervals.icu provide it? |
|---|---|---|
| `respect_zones_score` | actual zone times vs the **planned** zone | No — the source does not know our plan |
| `cardiac_drift_index` | heart-rate time series | No |
| `intervals_consistency_index` | power time series | No |
| `session_type_real` | our own classification rules | No |
| `variability_index` | `normalized_power / avg_power` | Derivable from two consumed values |
| `dominant_zone` | zone times | Derivable from consumed zone times |

These are used heavily by the post-activity coaching narrative (`app/llm/activity_analysis.py` references
them throughout) and are persisted as columns on `session_logs`. They are **not** training-load
calculations, so retaining them does not violate FR-016.

**Recommendation**: retain all six. They are Banister-specific analysis the source does not offer, they
are the substance of what makes the post-ride feedback distinctive, and dropping them would silently
degrade User Story 2's preserved behaviour (FR-030). FR-018's list should be read as illustrative rather
than exhaustive — but that reading should be made explicit in the spec rather than assumed.

**Consequence if retained**: `cardiac_drift_index` and `intervals_consistency_index` require the activity's
time series, so the ingestion path needs a streams fetch per qualifying activity, not just the activity
summary. That is an extra API call per activity — still comfortably within quota (R4), but it is a real
design consequence of this decision rather than a free one.

**⚠️ To confirm with a real key**: whether intervals.icu exposes per-activity streams, under what path, and
whether it also exposes zone times directly (which would let `time_in_zones_s` and `dominant_zone` be
consumed rather than computed).

---

## R6. What survives of the existing Strava pipeline

**Decision**: The three-layer split survives; only the bottom layer is replaced.

| Layer | Current | After |
|---|---|---|
| Fetch | `StravaActivityFetcher` — tiered stream fetching | **Replaced** by an intervals.icu client |
| Analyse | `SessionAnalyzer` — NP, TSS, zones, quality metrics | **Reduced**: stops computing TSS and zones (FR-017), keeps quality metrics (R5) |
| Match | `matching.py` — activity ↔ planned session | **Unchanged** (FR-033) |
| Notify | `webhook.py` — staged messages, highlight, PR detection | **Re-triggered by the poller** rather than an inbound webhook; content unchanged (FR-030) |

**Rationale**: the existing separation between ingestion, analysis and matching was designed precisely to
decouple the provider from the business logic (it is documented as such in `CLAUDE.md`). This migration is
the first real test of that design, and it holds: `matching.py` and `highlight.py` need no changes at all.

---

## R7. Removals and their collateral

**Decision**: Remove the Strava integration and manual session entry, but preserve perceived-exertion
capture, which currently shares an implementation with manual entry.

**Evidence** (from the audit recorded in spec 001): `app/bot/routers/session_log.py` contains two
near-duplicate handlers — `cb_rpe_manual` (manual logging) and `cb_rpe_strava` (post-ride RPE). Removing
"manual logging" wholesale would destroy the perceived-exertion step that User Story 2 depends on
(FR-031). Roughly 400 of that file's 775 lines go away; the surviving path keeps the RPE capture.

Also removed with the Strava path: `oauth_connections` (the table, now genuinely obsolete — spec 003
deliberately deferred dropping it to this feature), the OAuth flow, the signed-state handling, the inbound
webhook endpoint, and the Strava connection commands.

---

## R8. Structural debt to clear on the surviving path

**Decision**: Fix the four defects the spec-001 audit found, as part of this migration rather than after.

They are already written as outcomes in FR-038 through FR-041: post-activity context assembly moves out of
the bot layer, event handlers stop reaching past the repositories, personal-best detection stops requiring
a fabricated stand-in object, and the value whose availability depends on a duplicated guard condition
goes away.

**Rationale**: the surviving handler is being substantially rewritten anyway (its trigger changes from an
inbound webhook to the poller). Fixing these while the code is already open costs far less than a separate
pass, and FR-038's requirement — that context assembly be testable without simulating a conversation — is
what makes the new path testable at all.

---

---

## R9. Verified against the live account — supersedes the guesses above

The author supplied a key. Everything below is **measured**, not inferred: 54 activities (June–August
2026), 8 wellness days, and the raw streams of one ride.

### R9a. The activity payload carries 183 fields, not a handful

The premise of R5 — "intervals.icu does not provide our quality metrics" — was **largely wrong**. The
author's instinct to challenge it was correct. What the source actually provides:

| Our computation | Their field | Verdict |
|---|---|---|
| `variability_index` | `icu_variability_index` | **Identical.** Verified arithmetically: NP 207 ÷ avg 103 = 2.0097, their value is 2.0097086 |
| `time_in_zones_s` | `icu_zone_times` | **Theirs is richer** — `[{id:"Z1",secs:4791},…]` plus a sweet-spot bucket we do not compute |
| `tss` | `icu_training_load` | Also split as `power_load` / `hr_load` with `hr_load_type: "HRSS"` |
| `intensity_factor` | `icu_intensity` | Same value ×100 (71.379 = IF 0.714) |
| `normalized_power` | `icu_weighted_avg_watts` | Directly provided |
| `dominant_zone` | — | Trivially derived from `icu_zone_times` |
| `cardiac_drift_index` | `decoupling` | Same concept, **materially different number** — see R9b |
| `intervals_consistency_index` | `icu_intervals` (9 detected, full metrics each) | They detect the intervals; the consistency *score* is still ours, but built on their detection rather than our own |
| `respect_zones_score` | — | **Genuinely ours** — requires our plan, which the source cannot know |
| `session_type_real` | — | **Genuinely ours** — our own classification |

**Also available and currently not used at all**: `trimp` (82.87), `polarization_index` (1.67, Seiler —
spec 006 wants exactly this), `icu_efficiency_factor`, `icu_power_hr`, `icu_hrr` (heart-rate recovery with
start/end bpm), `icu_pm_ftp` / `icu_rolling_ftp` (eFTP tracking), `strain_score`, `icu_warmup_time` /
`icu_cooldown_time`, `compliance`.

`hr_load_type: "HRSS"` deserves special note: the entire Banister-TRIMP HRSS implementation in
`app/engine/tss.py` — the one documented at length in `CLAUDE.md` — computes a value the source already
provides.

### R9b. `decoupling` and our `cardiac_drift_index` disagree, and we cannot reproduce theirs

Computed our formula against the real streams of activity `i180170537` (10,206 samples):

```
our formula          22.937 %
intervals.icu         15.709 %
```

Attempts to reconcile, all failed:

| Variant tried | Result |
|---|---|
| All samples (our current behaviour) | 22.937 |
| Excluding zero-power samples (coasting) | −5.322 |
| Excluding warm-up and cool-down | 36.522 |
| Both combined | −5.782 |

**Conclusion, and it is the useful one**: this metric is extremely sensitive to sample-selection choices,
and reverse-engineering theirs is not worth the effort. Two different numbers under the same name is
exactly the divergence spec 001's metric-authority rule exists to prevent. **Consume `decoupling`; delete
ours.** FR-016 already requires this — the finding is that it applies more widely than R5 assumed.

### R9c. Null training load is real, common, and permanent

**8 of 54 activities (15%) have `icu_training_load: null`.** All eight have `has_heartrate: null` and
`device_watts: null` — no sensors at all. Every one has a populated `analyzed` timestamp, and
`analysis_issues` is null across all 54.

So the convention is **not** "null while analysis is pending" but **"null because it cannot be computed"**
— a permanent state for sensor-less activities, not a transient one to wait out.

This settles the most consequential unknown, and settles it in favour of the requirement being *more*
important than assumed: FR-020's "unknown must not become zero" would otherwise mis-record **15% of this
athlete's real rides as rest days**, silently deflating chronic load.

**Answering the author's question directly** — "how do we wait for analysis to finish?": we do not need to.
There is no pending state to wait for. `analyzed` is always set; a null load means no data to compute
from. The correct handling is to store null and carry on, not to poll again hoping it fills in.

### R9d. Wellness is entirely empty — this blocks spec 006, not this feature

The endpoint works and exposes `hrv`, `restingHR`, `sleepSecs`, `readiness`, `sleepScore`, `vo2max`,
`steps`, and more. But for this athlete, across all 8 days sampled:

```
hrv          0/8 days populated
restingHR    0/8
sleepSecs    0/8
readiness    0/8
ctl / atl    8/8   (computed by intervals.icu itself)
```

Capturing wellness (FR-023) still works and should still be built — it stores what is there. But **spec
006's readiness guardrails have no input today**. Those thresholds — HRV down 20%, resting HR up 5 bpm —
cannot fire against empty columns. That is a finding for spec 006's viability, surfaced here because this
is where it became knowable.

### R9e. Athlete profile confirms identity, with one inconsistency

`GET /athlete/0` returns the bound athlete (id `i000000`, name, sex, timezone, email) — satisfying FR-002
without needing the athlete id configured at all.

One oddity worth carrying forward: `icu_ftp` is **null on the athlete profile** but **290 on the activity**,
which also carries `icu_pm_ftp: 225` and `icu_rolling_ftp: 280`. Three different FTP figures. Which one
the plan generator should use is a real decision, not an obvious one.

### R9f. Endpoints and formats, confirmed working

```
GET /api/v1/athlete/{id}/activities?oldest=YYYY-MM-DD&newest=YYYY-MM-DD   → 200, JSON (no .csv needed)
GET /api/v1/athlete/{id}/wellness?oldest=…&newest=…                        → 200, JSON
GET /api/v1/athlete/0                                                      → 200, resolves to key's athlete
GET /api/v1/activity/{id}?intervals=true                                   → 200, includes icu_intervals
GET /api/v1/activity/{id}/streams?types=watts,heartrate                    → 200, full series
```

Still unverified: the shape of a rate-limited response (FR-013). Requires deliberately exceeding the
quota, which is not worth doing against a live account.

---

## Resolved unknowns

| Unknown | Resolution |
|---|---|
| Authentication scheme | Basic auth, `API_KEY` as username (R1, confirmed R9f) |
| Endpoint paths, date filtering, JSON format | Confirmed working against the live account (R9f) |
| Load / fitness / wellness field names | Confirmed, plus ~170 more fields than expected (R9a) |
| **Null vs. pending training load** | **Null = uncomputable, permanent, 15% of real activities (R9c)** |
| Are streams exposed? | Yes (R9f) |
| Are zone times exposed? | Yes, richer than ours (R9a) |
| Quota headroom at a 5-minute interval | ~6% of the daily allowance (R4) |

## Still unresolved

1. **Rate-limit response shape** (FR-013) — would require deliberately exhausting the quota.
2. **Which FTP the plan generator should use** — the profile says null, the activity says 290,
   `icu_pm_ftp` says 225, `icu_rolling_ftp` says 280 (R9e).
3. **What to do about the empty wellness data** — a spec 006 problem, but it needs an answer before
   spec 006 is worth building (R9d).

## Answered: the open question R5 raised

R5 asked whether the six quality metrics should be retained. **Verification changed the answer.** Only two
are genuinely ours (`respect_zones_score`, `session_type_real`), one is ours but should be rebuilt on the
source's interval detection (`intervals_consistency_index`), and **three should be deleted and consumed
instead** (`variability_index`, `cardiac_drift_index`, `dominant_zone` — along with `time_in_zones_s`,
which was never in question but is also provided).

This is a larger deletion than the spec anticipated, and it strengthens rather than weakens FR-017. It also
extends to `app/engine/tss.py`'s HRSS/TRIMP implementation, which computes `hr_load` and `trimp` — both
provided.
