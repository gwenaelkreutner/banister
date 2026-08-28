# Quickstart: Validating Training Guardrails

**Feature**: 006-training-guardrails | **Date**: 2026-08-28

How to prove this feature works. Each scenario maps to a user story and the success criteria in
[spec.md](./spec.md).

**Unlike spec 005, most of this is validated offline** — and that is a property of the feature, not a
shortcut. Guardrail evaluation is pure deterministic Python over data already captured (FR-022), so the
signals can be driven to any state with fixtures. Nothing here writes to the athlete's account; this
feature has no write path of its own (FR-023).

**The one thing fixtures cannot give you** is the state the real account is actually in — research R1 found
no HRV, no sleep, and no resting HR since 19 July, against a complete June/July RHR baseline. That
combination is Scenario 5's subject and is the most valuable live check in this feature.

---

## Scenario 0 — Record what the athlete's data actually contains

Do this first. It tells you which scenarios can be checked live and which need fixtures.

```bash
uv run python -m scripts.guardrail_state --describe
```

**Expect**: per signal, the number of days with data, the date range covered, and whether a baseline is
currently establishable. The expected shape today, per research R1:

- `ctl` / `atl` / `ramp_rate` — every day, baseline establishable
- `resting_hr` — 28 days, **ending 2026-07-19**, baseline establishable but **no current observation**
- `hrv`, `sleep` — nothing, no baseline

If HRV has appeared since this was written, the athlete has connected a device and Scenario 2 becomes
checkable live. That is the only thing that changes this picture.

## Scenario 1 — The athlete is warned before digging the hole (US1, SC-003, SC-008)

```bash
uv run pytest tests/test_engine/test_guardrails_workload.py -v
```

Then, in Telegram: ask the coach about this week's training.

**Expect**:

- The acute:chronic ratio is stated **with its range** and what the athlete's value means (FR-002) — never
  a bare number.
- The ramp rate is evaluated as a **second, independent** signal (research R4). A taper falling on both and
  a sustainable build rising on one must come out different — that is why there are two.
- When the ratio is above range, the recommendation **reduces** load (FR-003, SC-008). Check this is a
  constraint on what may be recommended, not a tone the model happened to adopt.
- Within range, **no warning is manufactured** (FR-005). This is the half that gets skipped; check it.
- Every finding ends in an action (FR-027, SC-003).

**Live check worth doing**: this athlete's real ramp rate hit **6.43** on 25/08 and sat above 4.5 for a
week, with `ATL/CTL` at 1.41 — an actual instance of the US1 scenario, not a constructed one. The coach
should be raising it.

## Scenario 2 — The coach notices the athlete is not recovered (US2)

```bash
uv run pytest tests/test_engine/test_guardrails_recovery.py -v
```

**Fixtures, not the live account** — research R1: the data does not exist. Drive each threshold with
synthetic wellness rows:

- HRV more than 20% below baseline ⇒ easy day directed, with the reason (FR-007).
- Resting HR ≥5 bpm above baseline ⇒ fatigue signal raised (FR-008).
- Several signals poor at once ⇒ treated as **more** significant than any alone (FR-009).
- Normal signals ⇒ **nothing raised** (US2 acceptance 4).
- Every raised signal states observed value, baseline, and threshold crossed (FR-010).
- Poor signals against a hard prescribed session ⇒ the conflict is **stated openly**, not resolved silently
  (FR-012).

## Scenario 3 — The numbers are true (US3, SC-001, SC-002)

```bash
uv run pytest tests/test_llm/test_response_verification.py -v
uv run python -m scripts.verify_corpus --last 100
```

**Expect**:

- Every metric value a response states matches the retrieved value (FR-017, SC-001).
- A metric absent from the `MetricRegistry` gets **no value stated** (FR-018, SC-002).
- A failing claim is **withheld and recorded**, and the rest of the response is delivered (FR-019, FR-021,
  research R6) — not the whole reply dropped.
- Data that could not be retrieved is **said to be missing** rather than answered around (FR-020).

**The check that matters most**: run the verifier over the real `chat_messages` corpus and read the
failures by hand. Research R5 measured a real response with fourteen numerals of which twelve were
durations and zones — if the verifier flags those, the anchoring is wrong and it will be switched off in a
week. False-positive rate is the number to watch here, not just the catch rate.

## Scenario 4 — Guardrails advise, they do not seize control (US4, SC-006)

```bash
uv run pytest tests/test_services/test_guardrail_authority.py -v
```

**Expect**:

- Firing every guardrail mutates **no** plan and **no** calendar entry (FR-023, SC-006).
- An accepted recommendation goes through the **existing** approval path — spec 004's `plan_modifier`, spec
  005's `authorize_publication` (FR-024). Confirm by grep that this feature introduces no new write path:
  it should have none of its own, which is what makes SC-006 structural rather than tested.
- A declined recommendation leaves the plan alone and is **not re-raised for the same occurrence**
  (FR-025) — drive this by evaluating twice across the `occurrence_key` boundary and confirming the second
  evaluation is silent, then across a day boundary and confirming it is not.
- Repeated override does **not** escalate: the signal keeps being stated, the recommendation stops being
  pushed (FR-026).

## Scenario 5 — Guardrails stay quiet when they cannot know (US5, SC-004, SC-005)

**The scenario this account is actually in, and the one to run live.**

```bash
uv run pytest tests/test_engine/test_guardrails_sufficiency.py -v
```

**Expect**:

- Insufficient history ⇒ **no signal fires**, and the insufficiency is stated where relevant (FR-013,
  SC-004).
- **Baseline present, current reading absent ⇒ reported as unknown.** This athlete has a complete June/July
  resting-HR baseline and nothing since 19 July. A system that reaches for the last known value, or for the
  baseline itself, will report "recovery normal" forever on six-week-stale data. Confirm the output is
  "I can't judge this" and not a comparison (FR-014, research R1).
- A single injected outlier never fires a finding alone (FR-011, SC-005) — inject one into otherwise normal
  data and confirm silence.
- The baseline is the athlete's own, never a population value (US5 acceptance 3).
- When history **becomes** sufficient, the signal activates with no action from the athlete (US5 acceptance
  4) — drive by appending fixture rows and re-evaluating.

## Scenario 6 — The athlete knows what this is not (US6, SC-009)

**Expect**:

- The disclaimer — not a physician, not a certified coach, sessions are suggestions — is present before the
  first coaching interaction and in the README (FR-028, SC-009). Research R7: it lands at the end of
  `/setup` and in the README, from a single constant, because the first-run moment itself belongs to spec
  007.
- Signals more consistent with illness than training fatigue ⇒ **suggests qualified advice** rather than
  coaching through it (FR-029).
- Reported pain or injury ⇒ **no diagnosis** (FR-030).

## Scenario 7 — Reproducibility and documentation (SC-007, SC-010)

```bash
uv run pytest tests/test_engine/test_guardrails_determinism.py -v
```

**Expect**:

- The same inputs produce the same findings every time (SC-010). No clock reads inside evaluation, no
  randomness, no LLM call anywhere in the path (FR-022).
- Every threshold in use is in `app/engine/guardrail_thresholds.py` **with its published source named**,
  and a reader can find each one without reading call sites (FR-016, SC-007). Read the module as if you
  were that reader.
- The EWMA caveat is present: `ATL/CTL` is 7d:42d and Gabbett's range was calibrated on 7:28
  (contracts §1). A correct number with mislabelled provenance still fails FR-016.

---

## The regression this feature must not skip

Research R2 found the shipping monotony index **drops rest days**, reporting **2.57** on a week whose
correct Foster value is **0.79** — and the athlete is already being told *"⚠️ Charge monotone"* on a week
that is in fact unusually varied. Three call sites consume it today (`weekly_recap.py` ×2,
`activity_analysis.py`).

```bash
uv run pytest tests/test_engine/test_weekly_snapshot.py -v
```

**Expect**: monotony computed over all seven days with rest days as zero; the athlete's real week comes out
below the 2.0 threshold; and all three call sites read the corrected value. This is a **user-visible false
alarm being fixed**, not a refactor — check the messages change.

---

## Definition of done

- [ ] Scenario 0 run, and each later scenario's live-vs-fixture status decided from what it reports
- [ ] All scenarios pass
- [ ] The monotony correction is verified at all three existing call sites, not just in the engine
- [ ] Every threshold documented with its source; a reader locates them without reading code (SC-007)
- [ ] The verifier's **false-positive** rate on the real corpus is checked by hand, not just its catch rate
- [ ] No new write path exists — confirmed by grep, not by assertion (SC-006)
- [ ] "Baseline present, observation absent" demonstrably reports unknown rather than normal (FR-014)
- [ ] A single injected outlier demonstrably fires nothing (SC-005)
- [ ] Full suite green; no new lint violations beyond the current baseline
- [ ] Disclaimer present before first coaching interaction and in the README (SC-009)
