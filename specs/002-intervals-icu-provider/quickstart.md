# Quickstart: Validating the intervals.icu Migration

**Feature**: 002-intervals-icu-provider | **Date**: 2026-08-27

How to prove this feature works. Each scenario maps to success criteria in [spec.md](./spec.md).

**This feature cannot be fully validated without a real intervals.icu account.** Unlike spec 003, where a
throwaway database was enough, the thing under test here *is* the integration with a live external
service. Scenarios marked 🔑 need a real key; the rest can be exercised against recorded fixtures.

---

## Prerequisites

```bash
uv sync --extra dev
uv run pytest tests/ -q       # baseline before starting: 190 passed, 14 skipped
uv run ruff check app/ tests/ # baseline: 269 violations — the gate is "no new ones"
```

Get an API key: intervals.icu → Settings → Developer Settings → API Key.

```env
INTERVALS_API_KEY=your_key_here
INTERVALS_ATHLETE_ID=i123456      # or leave unset — "0" resolves to the key's own athlete
```

---

## Scenario 0 🔑 — Settle the unknowns before writing code that assumes them

**Do this first.** [research.md](./research.md) lists five API behaviours that could not be verified
without a key. Each one is a thing the implementation would otherwise *guess*.

```bash
curl -u API_KEY:$INTERVALS_API_KEY \
  "https://intervals.icu/api/v1/athlete/0/activities?oldest=2026-08-01&newest=2026-08-27" | jq '.[0]'
```

**Determine, and write down:**

| Question | Why it matters |
|---|---|
| Is `icu_training_load` `null` or absent when analysis is incomplete? | **The most consequential.** Stored as `0.0`, a not-yet-computed load reads as a rest day and silently corrupts chronic load (FR-020) |
| Does the endpoint return JSON without a `.csv` suffix? | The documented example uses `.csv` |
| Are per-activity streams exposed, and where? | Determines whether two quality metrics survive (research R5) |
| Are zone times exposed directly? | Determines whether `time_in_zones_s` is consumed or computed |
| What does a rate-limited response look like? | FR-013 needs the real shape to back off correctly |

**Capture real payloads as test fixtures while you are here** — the mapper tests in Scenario 2 need them,
and fixtures recorded from a real account beat handwritten ones that encode the same assumptions the code
makes.

---

## Scenario 1 🔑 — Connecting is pasting one key (SC-001, FR-001/002/003)

```bash
docker compose up -d && docker compose logs -f app
```

**Expect**: the log names the athlete account it bound to. No browser, no consent screen, no callback URL,
nothing exposed.

**Then break it deliberately** — the failure paths matter as much as the happy one:

```bash
# Malformed key → startup must refuse, naming the setting and how to get a valid one (FR-003)
INTERVALS_API_KEY=nonsense docker compose up
# Absent key → same
INTERVALS_API_KEY= docker compose up
```

Both must **refuse to start**, not start in a half-working state.

---

## Scenario 2 — The mapper preserves meaning (SC-004, FR-015/016/020)

```bash
uv run pytest tests/test_providers/test_mapper.py -v
```

**Expect**, against the fixtures from Scenario 0:

- Every consumed metric equals the source's value exactly — no rounding, no derivation
- **A metric the source omits maps to `None`, never `0.0`** — assert this per field, not once
- Values captured under the thresholds in force stay stable when thresholds later change (FR-022)

The null-versus-zero assertion is the highest-value test in this feature. It is also the easiest to write
in a way that passes without proving anything: a fixture where every field happens to be populated tests
nothing. **Include a fixture with omitted fields.**

---

## Scenario 3 🔑 — A ride becomes a coaching moment (SC-002/003, FR-006..012, FR-030)

The real acceptance test. Do an activity, let it reach intervals.icu, then wait.

**Expect**: the full staged exchange, unchanged from before the migration — teaser, hero metric, verdict
with the RPE keyboard, then the narrative after answering. Only one alert.

**Then verify the properties that a single happy path does not show:**

```bash
# Exactly-once across a restart (FR-009)
docker compose restart app       # must NOT re-announce the same activity

# An edit is not a new activity (FR-010)
# → rename the activity on intervals.icu, wait one poll cycle: no second notification

# Backlog is bounded (FR-011)
docker compose stop app
# ... let several activities accumulate ...
docker compose start app         # every activity ingested; NOT one alert each
```

---

## Scenario 4 🔑 — The numbers match (SC-004, FR-015/016)

Open intervals.icu in a browser. Ask the coach the same questions.

**Expect**: identical values for training load, fitness, fatigue and form — sampled across at least four
weeks, not just today. Divergence here means something is being recomputed locally, which FR-016 forbids
and which is the failure spec 001's metric-authority rule exists to prevent.

---

## Scenario 5 🔑 — A new athlete does not start from zero (SC-007, FR-026..029)

```bash
docker compose down -v && docker compose up -d    # fresh store, real account
```

**Expect**: chronic load reflects real history, not a standing start. While importing, the coach says
history is still loading rather than presenting partial figures as complete.

**Interrupt it** (`docker compose restart app` mid-import): the import resumes without duplicating and
without leaving gaps (FR-027).

---

## Scenario 6 🔑 — Wellness is captured, and silent about it (FR-023/024/025)

**Expect**: HRV, resting heart rate and sleep retained against correct dates. A day with no reading is
stored as unknown, **distinguishable from zero**.

**And expect the coach to say nothing about readiness** — interpretation belongs to spec 006. If it starts
drawing conclusions here, this feature has exceeded its scope (FR-025).

---

## Scenario 7 — The old paths are gone (SC-009, FR-035/036/037)

```bash
grep -rn "strava\|Strava" app/          # → nothing
grep -rn "dialects.postgresql" app/     # → nothing (already true since spec 003)
```

**Expect**: no manual session entry anywhere, no OAuth flow, no signed state, no inbound activity webhook,
no Strava commands. **But** perceived-exertion capture still works (FR-031) — they shared an
implementation, and only one was meant to go.

---

## Scenario 8 — The boundary held (contracts/sport-provider.md)

```bash
git diff --stat <base>..HEAD -- app/engine/ app/llm/
```

**Expect**: nothing, or import-path updates only. Logic changes in the engine or LLM layers mean the
provider leaked past its boundary — the architecture's decoupling claim was weaker than believed, and that
is worth reporting rather than absorbing.

---

## Definition of done

- [ ] Scenario 0 completed **first**, with answers written into research.md rather than left as assumptions
- [ ] All scenarios pass
- [ ] The mapper's null-versus-zero test uses a fixture with genuinely omitted fields
- [ ] Full suite green; no new lint violations beyond the 269 baseline
- [ ] Post-activity notification verified against a **real ride** before Strava is removed, not after
- [ ] Engine and LLM layers show no logic changes
