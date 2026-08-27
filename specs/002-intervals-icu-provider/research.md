# Phase 0 Research: intervals.icu as Sole Training Data Source

**Feature**: 002-intervals-icu-provider | **Date**: 2026-08-27

Findings below come from intervals.icu's own documentation and forum (the developer documents the API
there rather than in a generated reference). **Nothing here was verified against a live account** — no API
key is configured in this project yet. Items marked ⚠️ are the ones that must be confirmed with a real key
before the code that depends on them is trusted; they are called out again as tasks rather than assumed.

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

## Resolved unknowns

| Unknown | Resolution |
|---|---|
| Authentication scheme | Basic auth, `API_KEY` as username (R1) |
| Endpoint paths and date filtering | Three endpoints, `oldest`/`newest` (R2) |
| Load / fitness field names | `icu_training_load`, `icu_ctl`, `icu_atl`, `icu_intensity`, `icu_ftp` (R3) |
| Wellness field names | `hrv`, `restingHR`, `sleepSecs` (R3) |
| New-activity detection strategy | Date-window poll plus locally recorded reported ids (R4) |
| Quota headroom at a 5-minute interval | ~6% of the daily allowance (R4) |
| Fate of the existing pipeline layers | Fetch replaced, analysis reduced, matching untouched (R6) |

## Unresolved — require a live API key ⚠️

These are **not** resolved and must not be treated as if they were. Each becomes a verification task before
the code depending on it is written:

1. **Is `icu_training_load` null or absent when analysis is incomplete?** FR-020's correctness depends on
   it, and getting it wrong corrupts chronic load silently.
2. **Does the activities endpoint return JSON without a `.csv` suffix, and is an unbounded query allowed?**
3. **Are per-activity streams exposed, and at what path?** Determines whether the two stream-derived
   quality metrics in R5 survive.
4. **Are zone times exposed directly?** Determines whether `time_in_zones_s` is consumed or computed.
5. **What exactly does a rate-limit response look like?** FR-013 requires retrying without exhausting the
   quota; that needs the actual response shape, not an assumption.

## Open question for the author

R5 is a genuine gap in the specification, not merely an implementation detail: six values are in neither
the "consume" list nor the "retain" list. The recommendation is to retain them, which implies an extra
streams fetch per activity. **This should be confirmed before implementation**, and FR-018 amended to say
so explicitly.
