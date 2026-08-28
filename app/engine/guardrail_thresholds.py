"""Every training-guardrail threshold, in one place, each next to its published source
(spec 006 FR-016, SC-007).

SC-007 requires a reader to locate every threshold *without reading code*. This module is
written to be read as documentation: a constant, its unit, and the study or convention it
comes from, on adjacent lines. Nothing downstream — neither `app/engine/guardrails.py`
nor `app/services/guardrail_service.py` — hard-codes any of these numbers.

They are **configurable defaults, not constants** (spec 006 Assumptions). Changing one is
a deliberate act; that it is deliberate is exactly what a single documented module buys
over values scattered through call sites.

── The acute:chronic ratio caveat, which is part of the contract, not a footnote ──

The ratio this feature evaluates is `wellness.atl / wellness.ctl` — a 7-day exponentially
weighted acute load over a 42-day exponentially weighted chronic load (spec 006 research
R3: the source's own figure is used because a locally computed load series double-counts
by ~1.8× on this athlete).

Gabbett's widely cited 0.8–1.3 "sweet spot" was calibrated on **rolling 7-day and 28-day
sums**, which is a different quantity. The EWMA formulation used here is not Gabbett's.
The range below is applied on the strength of Williams et al. (2017), "Better-coupled..."
(Br J Sports Med), which showed the EWMA form tracks injury risk at least as well as the
rolling form and flags rising risk earlier — and which retains a comparable sweet spot.
A threshold whose provenance is mislabelled fails FR-016 even when the number is right,
so this is stated here rather than assumed.
"""
from __future__ import annotations

# ── Workload: acute:chronic ratio (wellness.atl / wellness.ctl) ───────────────

ACWR_SAFE_LOW = 0.80
# Lower bound of the sweet spot. Gabbett (2016), as carried to the EWMA form by
# Williams et al. (2017) — see the module docstring. Below this, load has dropped
# relative to the chronic base (a taper, or detraining if sustained).

ACWR_SAFE_HIGH = 1.30
# Upper bound. Same source. Above this, recent load is out of proportion to what the
# athlete has absorbed — the US1 warning. FR-003 / SC-008: recommendations above this
# bound MUST reduce load.

ACWR_MIN_CTL = 25.0
# Below this chronic level the ratio is reported "not yet meaningful" rather than
# evaluated (spec 006 research open question 2). Rationale: on a long layoff CTL falls
# toward zero and a single first session back spikes ATL, producing an alarming ratio
# that reflects a thin chronic base rather than a real spike (the spec's own edge case).
# 25 CTL ≈ a sustained ~175 TSS/week — below that the athlete is building up, where a
# high ratio is expected, not a risk. Conservative default; tune against the athlete's
# own return-from-layoff history if one occurs. (This athlete sits at ~45, well clear.)

# ── Workload: CTL ramp rate (wellness.ramp_rate, source-computed) ─────────────

RAMP_RATE_CAUTION = 5.0
# CTL points gained per week. The conventional caution band for CTL ramp is 5–7
# (TrainingPeaks / Coggan practice; also the band intervals.icu itself flags). Consumed
# as-is from the source (spec 006 research R4), never recomputed.

RAMP_RATE_HIGH = 7.0
# Above this, the chronic base itself is climbing fast enough that injury/illness risk
# rises independently of the acute:chronic ratio — the second, independent workload
# signal (research R4: a taper falls on both, a hard build rises on the ratio with a
# flat ramp, only an unsustainable build rises on both).

# ── Workload: load uniformity (Foster monotony) ──────────────────────────────

MONOTONY_HIGH = 2.0
# Foster's monotony index = mean(daily load) / SD(daily load) over the 7-day week,
# **rest days counted as zero** (Foster 1998, "Monitoring training in athletes...").
# Above ~2.0 the week is monotonous enough that strain accumulates without the
# stimulus variation that drives adaptation — a risk in its own right (FR-004).
# NB spec 006 research R2: the value fed in here was previously computed over training
# days only, dropping the rest-day zeros that create the variance, and was biased
# toward false alarms. Corrected in app/engine/weekly_snapshot.py.

# ── Recovery: signals vs personal baseline ───────────────────────────────────

HRV_DROP_PCT = -20.0
# Percent below the athlete's own rolling HRV baseline that directs an easy day
# (spec 006 FR-007, stated as an absolute in the specification). HRV suppression of
# this magnitude is a well-established parasympathetic-withdrawal / non-recovery signal
# (Plews et al. 2013).

RHR_RISE_BPM = 5
# Beats per minute above the athlete's own rolling resting-HR baseline that raises a
# fatigue signal (spec 006 FR-008). A morning RHR elevation of ≥5 bpm is a classic
# overreaching / incipient-illness marker.

# ── Recovery: baselines ──────────────────────────────────────────────────────

BASELINE_WINDOW_DAYS = 28
# Rolling window over which each recovery baseline is computed. Long enough to average
# out day-to-day noise, short enough that the baseline adapts as fitness changes
# (FR-015). 28 days is the conventional monitoring window.

BASELINE_MIN_SAMPLES = 14
# Below this many readings within the window, the baseline is `None` and the signal is
# NOT evaluated (FR-013, FR-014, SC-004). Half the window is the minimum for the mean
# to be a meaningful "normal" rather than a small-sample artefact — the spec's own US5
# scenario ("baseline computed on four days of data").

# ── Single-reading protection ────────────────────────────────────────────────

OUTLIER_SD = 3.0
# A reading more than this many standard deviations from its baseline is treated as a
# probable device error and does NOT fire a finding on its own (FR-011, SC-005). A real
# threshold crossing must persist across ≥2 consecutive days to be raised.

# ── Response verification ────────────────────────────────────────────────────

VERIFY_TOLERANCE_PCT = 2.0
# A stated metric value within this percent of the retrieved value passes verification.
# Display rounding is not a lie: the prompt says CTL 45.9, a response saying "46" is
# honest (spec 006 research R5).
