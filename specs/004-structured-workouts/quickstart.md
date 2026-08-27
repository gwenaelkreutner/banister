# Quickstart: Validating Structured Workouts

**Feature**: 004-structured-workouts | **Date**: 2026-08-27

How to prove this feature works. Each scenario maps to a user story and the success criteria in
[spec.md](./spec.md).

Unlike spec 002, **this feature needs no live external account**. Everything here is deterministic engine
code, so every scenario is runnable offline against fixtures and the athlete's existing local database.
That makes the compatibility guarantees (SC-003, SC-004) genuinely checkable rather than sampled.

---

## Prerequisites — capture the baseline *before* changing anything

The whole of User Story 2 is a claim that nothing changed. That claim is unverifiable unless the "before"
is recorded first.

```bash
uv sync --extra dev
uv run pytest tests/ -q          # baseline: 238 passed, 13 skipped
uv run ruff check app/ tests/    # baseline: 226 violations — the gate is "no new ones"
```

Then snapshot the two behaviours that must not move:

```bash
# Matching + adherence over the athlete's real activity corpus, before the change
uv run python -m scripts.snapshot_session_behaviour --out baseline.json
```

That helper does not exist yet — creating it is the first task of the compatibility work, and it must be
written **before** `SessionSpec` changes, or there is nothing to compare against.

---

## Scenario 1 — A session says what to actually do (US1, SC-001, SC-002)

```bash
uv run pytest tests/test_engine/test_structured_sessions.py -v
```

**Expect**, over a freshly generated plan:

- Every session carries `steps` — including endurance and recovery rides, which appear as a single
  `steady` step rather than as a special case (FR-004).
- Repeated efforts appear as a `RepeatGroup` with a count, never as the same step listed N times (FR-003).
- Every step states a duration and a zone.
- `duration_minutes` equals the sum of the steps in **100%** of sessions (SC-002) — with a repeat group
  contributing `repeat × Σ inner durations`.

The last assertion is the one worth writing carefully. It is easy to satisfy trivially by deriving the
summary and then asserting the derivation against itself. **Assert against independently stated
expectations** for at least a few known templates — e.g. threshold 3×12r4 must total
`15 + 3×12 + 2×4 + 15 = 74` minutes — so the test can actually fail.

## Scenario 2 — Nothing that reads sessions broke (US2, SC-003, SC-004)

The core risk. ~20 read sites and ~14 construction sites (research R7).

```bash
# Same corpus, same command, after the change
uv run python -m scripts.snapshot_session_behaviour --out after.json
diff baseline.json after.json          # MUST be empty
```

**Expect**: byte-identical matching and adherence results (SC-004). Not "similar" — identical. A
difference here means the derived summary disagrees with what the previous code stored, and every
downstream number the athlete sees moves with it.

Then exercise the readers that a corpus diff does not cover:

```bash
uv run pytest tests/test_engine/ tests/test_analysis/ tests/test_services/ -q
```

and by hand in Telegram: `/plan`, `/week 2`, `/recap`, and a conversational plan modification through the
coach. The last one matters most — `plan_modifier.py` holds 7 construction sites and is LLM-driven, so
its output is not fully predictable from tests (FR-012).

## Scenario 3 — A contributor adds a session without touching code (US3, SC-006, SC-007)

```bash
cp sessions/threshold.yaml /tmp/backup
# add a new template to sessions/threshold.yaml — new id, valid structure
uv run pytest tests/test_engine/test_session_library.py -q
```

**Expect**: the new template loads and becomes selectable with **zero changes to any `.py` file**
(FR-016). If making it selectable required editing the generator, the library is decorative and FR-015 is
not met.

**Also expect** the coverage assertion to hold: every `(phase, workout_type)` the periodization can
request has at least one template (SC-007). Deliberately delete a template and confirm that test **fails**
— a coverage test that cannot fail proves nothing.

**And** confirm the failure modes are legible:

- a duplicate `id` → load error naming both files
- a `repeat: 1` → rejected (one structure, one representation)
- a zone the athlete's scheme does not define → error, not a silent substitution
- a request with no matching template → raises and names the unsatisfied request (FR-021), rather than
  quietly returning something else

## Scenario 4 — Intensities follow the athlete (US4, SC-008, SC-009)

```bash
uv run pytest tests/test_engine/test_fitting.py -v
```

**Expect**:

- Change the athlete's FTP, regenerate: every future session's absolute targets move (SC-008).
- Examine `session_logs` for completed sessions: **unchanged** (SC-008). This holds by construction —
  steps store zone codes only, and completed history lives in a different table — but assert it anyway,
  because "by construction" is exactly the kind of claim that stops being true after a refactor.
- Generate for an athlete with `coaching_mode: hr`: intensities present in heart-rate terms (SC-009).
- Generate for an athlete with **no** threshold: zone codes still presented, absolute targets simply
  absent — never fabricated (FR-027, Constitution Principle IV).

**Then the refusals** (FR-024), which are the part most likely to be skipped:

- a load target so low that the template cannot shrink within its `scaling` bounds → refuses, names the
  constraint
- a fitted session longer than the athlete's stated availability → refuses
- confirm neither case silently emits a clamped session — the old `max(40, min(120, …))` behaviour is
  precisely what this replaces (research R2)

## Scenario 5 — Plans created before the change still open (US5, SC-005)

```bash
uv run pytest tests/test_engine/test_structured_sessions.py -k legacy -v
```

Using a real pre-change plan document as a fixture — not a hand-written approximation of one, which would
encode the same assumptions the new code makes:

```bash
uv run python -c "
import json, sqlite3
c = sqlite3.connect('data/banister.db')
row = c.execute('SELECT plan_technical FROM training_plans LIMIT 1').fetchone()
open('tests/fixtures/plans/legacy_plan.json','w').write(row[0])
"
```

**Expect**: it loads, displays, matches activities and scores adherence, with sessions that have no steps
(SC-005). Where something requires steps, absence is reported rather than fabricated (FR-014) — check
this explicitly, because fabricating a plausible warm-up would look correct and be wrong.

**Also**: generate a new plan for the same athlete and confirm the old one is unaffected.

## Scenario 6 — Plan quality did not regress (FR-014a, FR-014b, SC-005a)

```bash
uv run python -m eval.runner            # before the change → eval/results/<timestamp>.json
# ... implement ...
uv run python -m eval.runner            # after
```

**Expect**: the harness still runs (FR-014a — it calls `generate_plan()` directly, so a change to
generation can break it), and the comparison shows no quality regression (FR-014b, SC-005a).

This scenario is also what settles the open question the plan deliberately left open: what "preserving
structural character" means numerically when fitting. It is a training-quality judgement, so it is
measured here rather than argued in advance.

## Scenario 7 — Reminders and the weekly review still read correctly (SC-005b)

Explicitly listed in the spec because these two are easy to forget: neither is covered by the matching or
adherence corpus, and both render session content to the athlete.

```bash
uv run pytest tests/test_services/test_weekly_recap.py -q
```

and trigger a reminder against a session that carries steps — confirm the message is correct and that a
rest day is still silence, not an empty session.

## Scenario 8 — Descriptions come from structure (US6, SC-010)

```bash
uv run pytest tests/test_engine/test_session_render.py -v
```

**Expect**:

- A description is produced from a session's steps, not retrieved from text embedded in generation logic
  (FR-028).
- The renderer takes a language and follows it (FR-029, to the extent this feature owns it).
- No stored session carries single-language text as its **only** description (FR-030).
- `grep -rn "description_fr" app/engine/plan_builder.py` shows no hardcoded French sentence construction
  remaining in generation logic.

**Scope boundary**: end-to-end language switching driven by the configured persona completes in spec 007,
which owns coach voice. `load_persona()` exists but is called from nowhere, and both specs name that
wiring as a shared prerequisite — doing it here as well would produce two different answers (research R6).
This scenario verifies the renderer's contract, not the persona wiring.

---

## Definition of done

- [ ] Baseline captured **before** any change to `SessionSpec` — otherwise SC-004 is unverifiable
- [ ] All scenarios pass
- [ ] `diff baseline.json after.json` is empty (SC-004), not merely "close"
- [ ] The library coverage test demonstrably **fails** when a template is removed
- [ ] Duration-equals-sum asserted against independently stated totals, not against its own derivation
- [ ] A legacy plan fixture taken from the real database, not hand-written
- [ ] Full suite green; no new lint violations beyond the 226 baseline
- [ ] `eval/` runs and shows no quality regression (SC-005a)
- [ ] Plan modification through the coach exercised by hand, not only by test (FR-012)
- [ ] If any Section 11 material was copied, a `NOTICE` with its MIT licence ships in the same change
      (spec 001 FR-028) — see plan.md §Phase boundaries
