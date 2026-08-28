# Research: Training Guardrails

**Feature**: 006-training-guardrails | **Date**: 2026-08-28

Phase 0 output. Every finding below was measured against **the live account and the live database**, not
assumed. Two of them invalidate an assumption the specification itself makes, and one of them is a
user-visible false alarm shipping today.

---

## R1 — The recovery signals US2 is built on do not exist for this athlete

**Question**: US2 (P1) evaluates HRV, resting HR and sleep against personal baselines. Is that data
there? Spec 002's research R9d found the wellness endpoint empty; is it still?

**Finding**, from the local `wellness` table (85 days, 2026-06-04 → 2026-08-27) and a fresh probe of the
source:

| Signal | Rows with data (local, 85 days) | Dates covered | In the source (Aug 2026) |
|---|---|---|---|
| `hrv` | **0** | — | absent |
| `resting_hr` | 28 | **2026-06-11 → 2026-07-19 only** | absent |
| `sleep_seconds` | **1** | — | absent |
| `ctl` / `atl` | 85 / 85 | every day | present, every day |

The resting-HR series is real data (41–78 bpm, plausible) that **stopped arriving on 19 July** and has not
resumed in the six weeks since — a device or app that was syncing and no longer is. HRV and sleep were
never present at all. The source *schema* is rich (46 fields, including `hrv`, `hrvSDNN`, `readiness`,
`sleepScore`, `soreness`, `stress`, `spO2`) but every one of them is empty for this athlete, because they
are populated by a device sync this athlete does not have.

`tempRestingHR` **is** populated every day — but it is a **boolean** flag meaning "this resting HR is
provisional", not a value. It is not a substitute and must not be mapped as one.

**Decision**: build US2 in full, and let **US5 carry it**. US5's "stay quiet when you cannot know" is not an
edge case in this feature — it is the state US2 will actually be in on the only real account. That makes
US5 the story to validate live, and US2 the story to validate against synthetic fixtures. Recording this
now stops a later reader concluding the recovery guardrails are broken when they are correctly silent.

**And the stopped series is the sharper test, not the empty one.** An athlete with *no* RHR history is the
easy case — nothing to compute a baseline from, nothing fires. This athlete has a **complete baseline from
June/July and no current observation**, which is precisely FR-014's trap: a system that reaches for "the
last known value" or "the baseline itself" as today's reading will conclude everything is normal, forever,
on data six weeks stale. The correct behaviour is that a baseline exists and today's signal is *unknown*,
and the two must not be collapsed. This is the case worth building the test around.

**Consequence for SC-004** ("zero findings fire when history is insufficient"): the insufficiency path is
the only path this account exercises, so it is the one that gets live validation.

**Not done**: acquiring the data. The spec's Scope excludes it ("acquiring new data — the signals here are
computed from what is already captured").

## R2 — The monotony index shipping today is wrong, and it is firing on the athlete

**The most consequential finding in this research.**

**Question**: the spec assumes "load uniformity is already computed by the existing engine using an
established formula" and this feature merely surfaces it (FR-004). Is that assumption true?

**Finding**: `app/engine/weekly_snapshot.py` computes Foster monotony as `mean/std` over **only the days
that have training**, discarding rest days. Foster's published formula is `mean/SD` over **all seven days,
rest days counted as zero load** — the zeros are precisely what create the variance, and a week with rest
days is the definition of a non-monotonous week.

Measured on this athlete's real last 7 days (loads `[0, 0, 108, 63, 0, 310, 338]`):

| | mean | SD | monotony |
|---|---|---|---|
| Foster, rest days included (correct) | 117 | 148 | **0.79** |
| Current code, rest days dropped | 205 | 138 | **2.57** |

The threshold used throughout the codebase is `> 2.0 = danger`. So on a week that is in fact **unusually
varied**, the current implementation reports monotony 2.57 and the athlete is told *"⚠️ Charge monotone —
varie les intensités"*. This is not theoretical: that string is already live in
`app/services/weekly_recap.py` (twice) and `app/llm/activity_analysis.py`.

Dropping rest days raises the mean and shrinks the spread, so the error is **systematically biased toward
false alarms** — the exact failure US5 exists to prevent, already shipping.

**Decision**: fixing this is in scope and is a prerequisite for FR-004, not an incidental cleanup. The
spec's assumption is corrected rather than inherited. All three existing call sites move to the corrected
value. Foster's companion **strain** (`weekly load × monotony`) becomes available for free and is worth
surfacing alongside it.

**Alternative considered**: keep the current value and re-calibrate the threshold upward to match its bias.
Rejected — FR-016 requires thresholds to be documented and inspectable against their published source, and
a threshold that only works against a miscomputed input is not inspectable, it is a coincidence.

## R3 — The local load series double-counts; the acute:chronic ratio must come from the source

**Question**: FR-001 needs "recent load ÷ load absorbed over a longer preceding period". Compute it from
local `session_logs` + `activities`, or derive it from the source's own CTL/ATL?

**Finding**: the local series disagrees with the source by roughly a factor of two.

```
local, naive (all session_logs + all activities)   acute 7d = 990
local, de-duplicated as chat.py does it            acute 7d = 819
implied by the source's own ATL (64.7, τ=7d)       acute 7d ≈ 450
```

`activities` and `session_logs` overlap: 51 activities exist, 2 of them dated on or after the active plan's
start, and the 5 session_logs cover rides that are also present as activities. `chat.py` de-duplicates with
one rule — `activity_date < plan.start_date` — and even after applying it the series is still ~1.8× the
source's. Any ratio built on that series inherits the error and multiplies it: a naive classic 7:28
computation on this athlete yields **ACWR 2.34**, which would fire the most severe possible warning on a
week the source considers a normal build.

This is the same class of defect CLAUDE.md already records from spec 002 (local TSB −36 vs source −18.8, a
17-point gap). The lesson has a second instance now.

**Decision**: derive the acute:chronic ratio as **`ATL / CTL`, read from the `wellness` table**, which is
the source's own figure. It is authoritative (Constitution Principle IV, CLAUDE.md "Autorité de la
source"), de-duplicated by construction, and present for **every day including rest days** — which a series
built from activities can never be. On this athlete it reads **1.41**, a plausible build-week value, against
the local series' implausible 2.34.

**Consequence — and it must be stated, not glossed**: `ATL/CTL` is a 7-day/42-day exponentially weighted
ratio, whereas Gabbett's published 0.8–1.3 "sweet spot" was calibrated on **rolling 7:28 sums**. The two are
not the same quantity and the range does not transfer unexamined. Williams et al. (2017) showed the EWMA
formulation is the better-behaved of the two and retains a comparable sweet spot, which is the ground for
using it — but the documented range must be published *as belonging to the EWMA form* (FR-016, SC-007), not
presented as Gabbett's number.

**Alternatives considered**:
- *Classic 7:28 from local logs.* Rejected on the evidence above: the input series is wrong.
- *Fix the de-duplication first, then compute locally.* Deferred, not rejected — worth doing on its own
  merits, but it makes the guardrail's correctness depend on a data-hygiene fix rather than on a figure the
  source already publishes. Wrong dependency direction for a P1 safety feature.

## R4 — The source already publishes a ramp-rate, and it is a real signal on this athlete

**Finding**, unprompted: the wellness payload carries `rampRate` — the source's own CTL gain per week —
populated on **every** day sampled, and it is not currently ingested (`ingest_wellness` stores only
`ctl`/`atl`/`hrv`/`restingHR`/`sleepSecs`/`weight`).

Real values over the last eleven days: `0.86, 2.72, 2.16, 2.11, 3.17, 5.19, 5.07, 6.43, 5.36, 4.62, 4.51`.

A CTL ramp above roughly 5–7 points per week is the conventional caution band. This athlete peaked at
**6.43** on 25/08 and sat above 4.5 for a week — an actual, live instance of exactly the situation US1
describes ("building for three weeks and feels good, so they keep pushing").

**Decision**: ingest `rampRate` and use it as a **second, independent workload signal** alongside the
ATL/CTL ratio. Consumed as-is, never recomputed (Principle IV). Two signals derived from the same PMC are
not redundant here: the ratio answers "is today's load out of proportion to what you've absorbed", the ramp
answers "how fast is the absorbed level itself moving" — a taper has a falling ramp and a falling ratio, a
sustainable build has a rising ratio and a flat ramp. The spec's own edge cases ("a planned taper produces a
falling ratio that must not be read as detraining") need both to be distinguishable.

Schema addition: one nullable `ramp_rate` column on `wellness`, one Alembic revision.

## R5 — Response verification must be keyword-anchored, not "extract every number"

**Question**: FR-017–FR-021 require checking that every value a response states matches what was retrieved.
How, without either missing claims or drowning in false positives?

**Finding**, from the real `chat_messages` corpus: the numbers in an actual coaching reply are
overwhelmingly **not metric citations**. A representative response contains
`['3', '15', '3', '2', '15', '2', '3', '30', '2', '2', '30', '1', '369', '221']` — of which everything but
`369` and `221` is a duration ("3h15", "2h30") or a zone ("Z2"). A naive "extract all numerals and verify"
would flag twelve false positives to catch two real claims, and a check that cries wolf gets switched off.

**Decision**: verification is **anchored on metric vocabulary**. A number is a claim to be checked only when
it sits adjacent to a term naming a metric the system actually retrieved — CTL, ATL, TSB, TSS, FTP, ratio,
monotonie, HRV, bpm, W, `%`. Everything else is prose and is not the verifier's business. Concretely:

1. Context assembly registers every metric it puts in front of the model into a `MetricRegistry`
   — `{name: value}`, the single source of truth for "what was retrieved" (FR-018, FR-020).
2. After generation, the response is scanned for `(metric term, number)` pairs.
3. Each pair is compared to the registry with an explicit tolerance (display rounding is not a lie: the
   prompt says CTL 45.9, the response saying "46" must pass).
4. A number anchored to a metric term **absent from the registry** is a fabrication (FR-018).
5. Failures are recorded with the claim and the expected value (FR-021).

**Alternatives considered**:
- *Ask a second LLM to check the first.* Rejected outright: FR-022 forbids the model performing evaluation,
  and a non-deterministic checker cannot satisfy SC-010's reproducibility requirement.
- *Structured output — make the model emit its numbers as fields.* Rejected for now: it constrains the
  conversational voice the product is built around, and it verifies only what the model chose to declare,
  not what it wrote in the prose. Worth revisiting if anchoring proves too lossy.

**Known limit, recorded rather than hidden**: anchoring trades recall for precision. A fabricated number
phrased with no metric word nearby will pass. That is the deliberate trade — SC-001 and SC-002 are measured
over a corpus, and a verifier the athlete trusts is worth more than one that catches every case and is
ignored.

## R6 — Withholding must degrade gracefully, and one path already exists

**Question**: FR-019 says a response failing the check is "corrected or withheld rather than sent". What
does the athlete see?

**Finding**: `run_chat` already returns a plain `response_text` that `chat.py`'s caller sends verbatim, and
the codebase already has a precedent for a deterministic fallback replacing an LLM answer — CLAUDE.md
records the `finish_reason=length` case where a null LLM response is "silently absorbed by the deterministic
fallback". That silence is called out there as a hazard.

**Decision**: a failed check **withholds the specific claim, not the whole answer**, and says so. Dropping
an entire reply because one figure is off teaches the athlete the coach is broken; the honest behaviour is
the one FR-020 already describes for missing data — say what is not known. Correction (substituting the
right value) is deliberately **not** attempted: rewriting a sentence around a corrected number is a
generation task, and doing it deterministically produces stilted text that reads worse than an honest
omission.

## R7 — The disclaimer belongs here, and its delivery point belongs to spec 007

**Finding**: FR-028 requires the disclaimer "at first use". There is no first-run flow yet — that is spec
007 ("first-run and goals"), and `app/core/persona.py`'s `load_persona()` is still uncalled.

**Decision**: this feature owns the **text and the requirement**; spec 007 owns the first-run moment. Here
the disclaimer is placed where a first use demonstrably passes today — the end of `/setup` onboarding and
the project README — with a single constant so 007 relocates it rather than rewriting it. FR-029 (refer to
qualified advice) and FR-030 (never diagnose) are prompt-level rules and are enforced where the other
response rules already live, in `app/llm/prompts.py`.

---

## Open questions carried into implementation

1. **The EWMA acute:chronic range.** R3 settles the *quantity* (`ATL/CTL` from source) but the published
   range for the EWMA form is less crisply agreed than Gabbett's 0.8–1.3. The default lands in a documented
   constants module (FR-016) where changing it is a deliberate act, and the value ships with its citation
   next to it. Resolve the exact band when writing that module, with the source named in the file.
2. **Whether the ratio needs a rest-day guard.** `ATL/CTL` on a long layoff tends to a small number over a
   smaller one and can spike on the first session back — the spec's own edge case. A minimum-CTL floor
   below which the ratio is reported as "not meaningful yet" is the likely answer, and is the same
   machinery as US5's insufficiency gate. Decide the floor against this athlete's own history.
3. **What a "single anomalous reading" means for a signal with no data** (FR-011, SC-005). The outlier
   guard is specified for wellness signals, which R1 shows are empty. It still needs building and testing
   against synthetic data; the question is only whether the same guard should also apply to the load
   signals, where one enormous ride genuinely can move ATL/CTL in a day. Leaning yes, decided when the
   signal evaluator is written.
