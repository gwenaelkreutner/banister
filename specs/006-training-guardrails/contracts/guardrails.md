# Contract: Training Guardrails

**Feature**: 006-training-guardrails | **Date**: 2026-08-28

Three interfaces cross a boundary in this feature: the **threshold registry** (a contract with a reader —
SC-007 requires it be findable without reading code), the **finding presented to the athlete** (a contract
with a person — SC-003 is specific about its parts), and the **response verification** boundary between
what was retrieved and what the model may say (FR-017–FR-021).

---

## 1. The threshold registry

Every value below lives in `app/engine/guardrail_thresholds.py`, each next to its published source. This
table is the contract; the module is its implementation and must not disagree with it.

| Name | Default | Applies to | Source / basis |
|---|---|---|---|
| `ACWR_SAFE_LOW` | `0.80` | `ATL/CTL` | Gabbett's sweet spot, **as adapted to the EWMA form** (Williams et al. 2017) — see the caveat below |
| `ACWR_SAFE_HIGH` | `1.30` | `ATL/CTL` | idem |
| `ACWR_MIN_CTL` | *TBD* | `ATL/CTL` | below this chronic level the ratio is reported "not yet meaningful" — research open question 2 |
| `RAMP_RATE_CAUTION` | `5.0` | `wellness.ramp_rate` | CTL points gained per week; the conventional caution band is 5–7 |
| `RAMP_RATE_HIGH` | `7.0` | `wellness.ramp_rate` | idem |
| `MONOTONY_HIGH` | `2.0` | corrected Foster monotony | Foster (1998); the threshold already used across this codebase |
| `HRV_DROP_PCT` | `-20.0` | HRV vs baseline | spec FR-007, stated as an absolute in the specification |
| `RHR_RISE_BPM` | `+5` | resting HR vs baseline | spec FR-008 |
| `BASELINE_WINDOW_DAYS` | `28` | all recovery baselines | rolling, so the baseline adapts (FR-015) |
| `BASELINE_MIN_SAMPLES` | `14` | all recovery baselines | below this the baseline is `None`, never a default (FR-013) |
| `OUTLIER_SD` | `3.0` | single readings | a reading beyond this many SD from baseline cannot fire alone (FR-011) |
| `VERIFY_TOLERANCE_PCT` | `2.0` | response verification | display rounding is not a lie (research R5) |

**The caveat is part of the contract, not a footnote.** `ATL/CTL` is a 7d:42d exponentially-weighted ratio.
Gabbett's 0.8–1.3 was calibrated on **rolling 7:28 sums**, a different quantity. The range is used here on
the strength of Williams et al. (2017) showing the EWMA formulation behaves comparably and detects risk
earlier — but the module must say so in the same breath as the number. A threshold whose provenance is
mislabelled fails FR-016 even when the number happens to be right.

**Values marked *TBD*** are resolved when the module is written, against this athlete's own history, and
the resolved value ships with its reasoning. They are not left to a call site to invent.

## 2. The finding presented to the athlete

SC-003: *every* finding includes the observed value, the reference point, and a recommended action. The
`GuardrailFinding` type makes an actionless finding unconstructible, so this is enforced at the type rather
than in review.

```
⚠️ Ta charge monte plus vite que tu ne l'absorbes

  Rapport aigu/chronique : 1.41
  Plage de référence     : 0.80 – 1.30
  Seuil franchi          : 1.30

→ Garde le bénéfice sans le risque : remplace la séance d'intervalles de jeudi
  par 1h en Z2. Tu veux que je te le propose ?
```

### Rules

- **The value, the reference, and the action are all present.** A finding that states a number without
  saying what to do about it is the failure FR-027 names.
- **A number is never presented alone.** "Ton ratio est à 1.41" is meaningless without the range; SC-003
  requires the reference point in the same breath.
- **The recommendation reduces load** when the ratio is above range (FR-003, SC-008). It is a hard
  constraint on what may be recommended, not a tone preference.
- **The athlete is asked, never told.** The finding ends in an offer. Applying it goes through the existing
  approval path (FR-024) — this feature has no write path of its own.
- **Simultaneous findings are ranked by severity and both stated.** The spec's edge case ("two guardrails
  fire with contradictory implications") is resolved by stating both and letting the athlete decide, never
  by silently picking one.
- **A declined occurrence is not re-raised** (FR-025) but the signal keeps appearing in the coach's context
  (FR-026). Declining stops the nagging, not the honesty.

### When a signal cannot be evaluated

```
Je ne peux pas encore juger ta récupération : je n'ai pas de mesure de FC de repos
depuis le 19/07, et aucune donnée de VFC. Ta charge, elle, je la vois.
```

- The insufficiency is **stated, not silent**, where relevant (FR-013).
- Missing is never rendered as normal (FR-014). "Ta récupération est bonne" on absent data is the specific
  lie this contract exists to prevent.
- A baseline that exists does **not** license reporting a current value that does not (research R1): this
  athlete has a June/July resting-HR baseline and no reading since. The correct output is the message
  above, not a comparison against six-week-old data.

## 3. Response verification

**The boundary**: `MetricRegistry` is assembled while the context is built and records exactly what was put
in front of the model. It is the definition of "retrieved".

```
retrieved: {"ctl": 45.9, "atl": 64.7, "tsb": -18.8, "acwr": 1.41, "monotony": 0.79}
```

**A claim** is a `(metric term, number)` pair found in the response — a number is checked only when it sits
adjacent to a term naming a metric (CTL, ATL, TSB, TSS, FTP, ratio, monotonie, VFC/HRV, bpm, W, `%`).
Everything else is prose: research R5 measured a real response containing fourteen numerals of which twelve
were durations and zones, so an unanchored checker would raise twelve false alarms to catch two claims.

| Outcome | Condition | Action |
|---|---|---|
| **pass** | claim matches the registry within `VERIFY_TOLERANCE_PCT` | deliver unchanged |
| **mismatch** | claim's metric is in the registry, value disagrees | withhold **the claim**, record failure (FR-019, FR-021) |
| **unretrieved** | claim's metric is absent from the registry | withhold **the claim**, record failure (FR-018) |

### Rules

- **The claim is withheld, not the response** (research R6). Dropping an entire reply over one figure
  teaches the athlete the coach is broken; the honest behaviour is the one FR-020 already describes for
  missing data — say what is not known.
- **Correction is not attempted.** Rewriting a sentence around a substituted number is a generation task;
  doing it deterministically produces text that reads worse than an honest omission.
- **The check is deterministic** (FR-022). It is never performed by a language model — a non-deterministic
  checker cannot satisfy SC-010's reproducibility requirement, and FR-022 forbids the model evaluating
  anything.
- **Every failure is recorded** with the claim verbatim and the expected value (FR-021), because SC-001 and
  SC-002 are stated as measurements over a corpus and an unrecorded failure is an unmeasurable one.

### Known limit

Anchoring trades recall for precision: a fabricated number phrased with no metric word nearby passes. This
is the deliberate trade, recorded here rather than discovered later — a verifier the athlete trusts is
worth more than one that catches every case and gets ignored.

## 4. Guarantees

| Guarantee | Requirement |
|---|---|
| All guardrail evaluation is deterministic Python; the model narrates findings it was given and produces none | FR-022, SC-010 |
| No finding fires without enough history to establish a baseline; the baseline is the athlete's own | FR-006, FR-013, SC-004 |
| Missing data is unknown, never a default — including when a baseline exists but the current reading does not | FR-014 |
| A single anomalous reading never fires a finding alone | FR-011, SC-005 |
| Every finding carries observed value, reference point, and action | FR-027, SC-003 |
| Above-range load ⇒ every subsequent load recommendation reduces load | FR-003, SC-008 |
| No plan or calendar change occurs from a guardrail firing; acceptance goes through the existing approval path | FR-023, FR-024, SC-006 |
| A declined occurrence is not re-raised; the signal is still stated | FR-025, FR-026 |
| Every stated metric value matches what was retrieved; unretrieved metrics get no value | FR-017, FR-018, SC-001, SC-002 |
| Every threshold is documented with its source and findable without reading code | FR-016, SC-007 |
| No diagnosis; illness-consistent signals get a referral | FR-029, FR-030 |
