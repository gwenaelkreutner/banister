# Implementation Plan: Structured Workouts and Session Library

**Branch**: `004-structured-workouts` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-structured-workouts/spec.md`

## Summary

Make a planned session carry its actual steps — warm-up, efforts, recoveries, repeats, cool-down —
instead of collapsing to one zone and one duration, and move session construction out of generator code
into an editable library.

The central finding from Phase 0 ([research.md](./research.md) R1) reframes the work: **the generator
already computes the structure and then throws it away.** `plan_builder.py` holds interval structures as
`(sets, work_min, rest_min, label)` tuples with explicit warm-up and cool-down constants, and
`_structure_duration()` is literally `warmup + sets*work + (sets-1)*rest + cooldown`. What reaches
`SessionSpec` is the sum plus a French label string.

So the approach is **additive and subtractive, not inventive**: add a `steps` field that preserves what
is already known, derive the existing summary attributes from it, and lift the hardcoded tuples into a
data library. Existing readers keep working because the summary attributes they read survive with
identical meaning.

The risk is not in the new structure. It is in the ~14 places that *construct* sessions (7 in
`plan_builder.py`, 7 in `plan_modifier.py`) and the ~20 that read them, and in plans already stored under
the old shape. The task budget goes there.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: Pydantic v2 (schemas), PyYAML (library files — already a dependency, used by
`app/core/persona.py`), pytest

**Storage**: SQLite via SQLAlchemy async. Plans are stored as JSON documents in
`training_plans.plan_technical` and re-validated through `TrainingPlanSchema.model_validate()` on every
read — **no per-session columns, therefore no database migration for this feature** (research R8).

**Testing**: pytest. Constitution Principle V and spec FR-014c both require engine changes to ship with
tests. Additionally the offline evaluation harness (`eval/`) must keep running (FR-014a) and must produce
a before/after quality comparison (FR-014b, SC-005a).

**Target Platform**: Self-hosted single-process Linux container

**Project Type**: Single Python application (bot + API + deterministic engine)

**Performance Goals**: Not a factor. Plan generation is a one-shot operation at `/setup`; the library is
tens of templates loaded once.

**Constraints**:
- Additive only — existing summary attributes retain their current meaning (FR-008)
- Summary derived from steps, never stored independently (FR-009)
- Matching and adherence scoring must produce byte-identical results (FR-010, FR-011, SC-004)
- Plans stored under the previous shape must keep loading (FR-013)
- Templates express intensity relatively; nothing absolute is persisted on a step (FR-022, FR-025)

**Scale/Scope**: ~15 templates cover the entire session vocabulary the periodization can request
(research R4). `SessionSpec.workout_type` is a 4-value `Literal`; variety lives in interval detail.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Deterministic engine, zero LLM load calculation** | **PASS.** Everything added here is pure Python in `app/engine/`. Steps, fitting arithmetic and derived summaries are deterministic. The LLM's only contact with the change is reading richer context (`llm/tools.py`, `llm/prompts.py`) and the existing plan-modification tool, neither of which computes load. |
| **II. Single-user, local-first** | **PASS.** No new external dependency, no network access, no new configuration. The library is local files. |
| **III. Clean layered architecture** | **PASS with a design obligation.** The library loader must live in `app/engine/` and import nothing from `bot/` or `llm/`. Library *content* lives in a top-level `sessions/` directory, mirroring the established `personas/` precedent — contributor-editable data outside the code that consumes it, which is exactly what FR-015 asks for. |
| **IV. Explicit data provenance, never estimate silently** | **PASS, and load-bearing.** FR-014 (report absence of steps rather than fabricate), FR-024 (fitting refuses rather than producing an unrideable session) and FR-027 (no threshold → present relative intensity, never a fabricated absolute) are all direct applications of this principle. Research R2 found the existing silent duration clamp to be a latent violation; this feature converts it into an explicit refusal. |
| **V. Engine logic is test-covered** | **PASS.** FR-014c restates the requirement. `plan_builder`, `plan_modifier` and the new library are all engine code and all get tests. |

### Finding: the constitution is stale relative to the code it governs

Not a violation by this feature, but recorded because the constitution is the authority a future review
would check against, and it currently describes a system that no longer exists:

- "Database is PostgreSQL (Supabase-hosted or local Docker) ... asyncpg" — false since spec 003 (SQLite + aiosqlite)
- "Strava OAuth state MUST remain HMAC-signed", "the optional Strava/sport-data integration" — Strava was removed in spec 002 and the source is now mandatory
- "tests under `tests/test_engine/` or `tests/test_strava/`" — `tests/test_strava/` no longer exists (now `tests/test_analysis/` and `tests/test_providers/`)
- "Migrations ... applied manually (Supabase SQL editor) or via `migrations/init.sql`" — false; Alembic applies them automatically at startup and `init.sql` was deleted
- "FastAPI (webhooks/OAuth)" — no OAuth path remains

Amending the constitution is a deliberate, separately-versioned act under its own Governance section, so
it is **not** done as a side effect of this plan. Raised here as a tracked item.

## Project Structure

### Documentation (this feature)

```text
specs/004-structured-workouts/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── session-library.md
├── checklists/
│   └── requirements.md  # pre-existing
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
sessions/                          # NEW — the library, contributor-editable (mirrors personas/)
├── README.md                      # how to add a template; what each field means
├── endurance.yaml
├── long-ride.yaml
├── recovery.yaml
├── sweet-spot.yaml
├── threshold.yaml
├── vo2.yaml
└── race-week.yaml

app/engine/
├── schemas.py                     # MODIFIED — Step, RepeatGroup; SessionSpec gains `steps`
├── session_library.py             # NEW — load, index, select templates (no bot/llm imports)
├── fitting.py                     # NEW — fit a template to a week's load target; refuse when unrideable
├── session_render.py              # NEW — describe a session from its structure (FR-028/029/030)
├── plan_builder.py                # MODIFIED — selects from the library instead of building inline
├── plan_modifier.py               # MODIFIED — 7 construction sites must emit consistent steps
└── zones.py                       # unchanged — already provides relative + resolved intensity (R5)

tests/test_engine/
├── test_session_library.py        # NEW — coverage of every (phase, type); deterministic selection
├── test_fitting.py                # NEW — structural character preserved; refusal cases
├── test_session_render.py         # NEW — description from structure, language-parameterised
├── test_structured_sessions.py    # NEW — steps↔summary consistency; legacy plans without steps
├── test_plan_builder.py           # MODIFIED — existing assertions must still hold
└── test_plan_modifier.py          # MODIFIED — modified sessions stay internally consistent

eval/                              # exercised, not restructured (FR-014a/b, SC-005a)
```

**Structure Decision**: Single project, existing layout. The one new top-level directory is `sessions/`,
placed at the repository root rather than inside `app/` deliberately: FR-015 requires the library to be
"readable and editable without modifying plan generation logic", and this project already has a working
precedent for exactly that shape — `personas/*.yaml` loaded by `app/core/persona.py`. Reusing that
convention means a contributor who understands training but not the codebase has one obvious place to
look, and the loader keeps YAML parsing out of `plan_builder.py`.

Three new engine modules rather than one, because they have genuinely different reasons to change:
selection (which template), fitting (how much of it), and rendering (how to describe it) each evolve
independently, and rendering is the one that spec 007 will later extend for coach voice.

## Complexity Tracking

No Constitution Check violations to justify. The table is intentionally empty.

## Phase boundaries and known scope limits

Recorded so they are decisions rather than discoveries during implementation:

1. **Coach-voice wiring is spec 007's, not this feature's** (research R6). `load_persona()` exists, works,
   and is called from nowhere. Both this spec and spec 007 name it as a shared prerequisite. This feature
   delivers a renderer that *takes* a language and produces text from structure (FR-028, FR-030);
   end-to-end language switching driven by the configured persona (FR-029) completes in spec 007. Doing
   the wiring in both places would produce two different answers, which is what the spec warns against.

2. **Section 11 material is not imported** (research R3). It is referenced in the spec but exists nowhere
   in the repository. The library is built from the project's own already-reviewed structures, so no
   third-party attribution obligation is triggered. **If** Section 11 content is later copied verbatim, a
   `NOTICE` carrying its MIT licence must land in the same change (spec 001 FR-028) — recorded as a task
   precondition.

3. **Spec 001's open-source-release residue is out of scope here but real**: no `LICENSE`, no
   `CONTRIBUTING`, no attribution file, and `.github/workflows/ci.yml` triggers on `main` while the
   repository is on `master`, so CI has never executed once. Tracked separately; noted because FR-014c
   and Constitution Principle V both assume a working test gate, and today that gate is not actually
   running on any push.

4. **Fitting arithmetic is settled empirically, not in advance** (research, open question 1). What
   "preserving structural character" means numerically is a training-quality judgement; the `eval/`
   harness already exists and SC-005a already demands a before/after comparison, so the comparison
   decides it rather than an argument in the plan.
