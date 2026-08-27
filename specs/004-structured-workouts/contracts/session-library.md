# Contract: Session Library File Format

**Feature**: 004-structured-workouts | **Date**: 2026-08-27

This is the interface this feature exposes to people rather than to code. FR-015 and FR-016 require that a
template be addable **without modifying plan generation logic**, and FR-017 requires each template to
state why it exists — so the authored file format *is* the contract, and it is what a training-literate
contributor who has never read the codebase must be able to work from.

Files live in `sessions/*.yaml` at the repository root, following the same convention as the existing
`personas/*.yaml`. One file may hold several templates of the same family.

---

## File shape

```yaml
# sessions/threshold.yaml
templates:
  - id: threshold-3x12
    workout_type: intervals
    family: threshold
    phases: [build, peak]

    purpose: Raise the power sustainable at lactate threshold.
    intent: >
      Three sustained efforts at the top of Z4 with short recoveries, long enough to
      accumulate threshold time without the session becoming a time trial.
    suits: >
      Build and peak phases, for an athlete already comfortable with 20 minutes of
      continuous tempo. Too demanding as a first intensity session of a block.

    structure:
      - kind: warmup
        duration_minutes: 15
        zone_code: Z1
      - repeat: 3
        steps:
          - kind: work
            duration_minutes: 12
            zone_code: Z4
          - kind: recovery
            duration_minutes: 4
            zone_code: Z1
      - kind: cooldown
        duration_minutes: 15
        zone_code: Z1

    scaling:
      repeat_range: [2, 4]
      work_minutes_range: [10, 20]
```

## Field reference

### Template identity

| Field | Required | Rule |
|---|---|---|
| `id` | yes | Unique across the whole library. Stable — selection determinism and tests refer to it. Kebab-case by convention. |
| `workout_type` | yes | One of `long_ride`, `intervals`, `endurance`, `recovery`. Must match what the periodization can request; this is the same closed set `SessionSpec.workout_type` already uses. |
| `family` | yes | Finer grain: `sweet_spot`, `threshold`, `vo2`, `endurance`, `long_ride`, `recovery`, `race`. Distinguishes templates that share a `workout_type`. |
| `phases` | yes | Non-empty subset of `base`, `build`, `peak`, `taper`. The template is selectable only in these phases. |

### Rationale — required, and not decoration

| Field | Required | Purpose |
|---|---|---|
| `purpose` | yes | One sentence: what adaptation this session targets. |
| `intent` | yes | What the structure is doing and why it is shaped that way. |
| `suits` | yes | When to use it, and — as valuable — when not to. |

FR-017 makes these mandatory because a library nobody can evaluate is not reviewable, and an
unreviewable library is worse than inline code: it looks like content while hiding the same judgements.
A reviewer must be able to disagree with a template on training grounds without reading Python.

### Structure

`structure` is an ordered list. Each element is either a **step** or a **repeat group**.

**Step**

| Field | Required | Rule |
|---|---|---|
| `kind` | yes | `warmup`, `work`, `recovery`, `cooldown`, or `steady` |
| `duration_minutes` | yes | Integer `> 0` |
| `zone_code` | yes | `Z1`–`Z6`. **Relative only** — never watts, never bpm |

**Repeat group**

| Field | Required | Rule |
|---|---|---|
| `repeat` | yes | Integer `>= 2`. A count of 1 is rejected: express it as plain steps so one structure has one representation |
| `steps` | yes | Non-empty list of steps. Groups do not nest |

Rules that hold for every template:

- A session with no internal structure is still expressed as steps — one `steady` step — not as a
  special case (FR-004).
- Intensity is always a zone code. Absolute targets are resolved per athlete at presentation time from
  the plan's zones, which already exist in both power and heart-rate form. Writing watts into a template
  would break the moment the athlete's threshold changed (FR-022, FR-025).
- A template referencing a zone the athlete's zone scheme does not define is a load-time error, not a
  silent substitution.

### Scaling

Optional. Declares how far fitting may adapt this template to hit a week's load target while keeping its
character recognisable (FR-023). Omit it and the template is fixed: selected as written, or not at all.

| Field | Meaning |
|---|---|
| `repeat_range` | `[min, max]` repetitions the structure tolerates |
| `work_minutes_range` | `[min, max]` duration of a single work step |
| `steady_minutes_range` | `[min, max]` total, for unstructured sessions |

Bounds live on the template rather than in the fitter so that a contributor can add a template with
different elasticity without touching generator code — which is precisely what FR-016 asks for.

---

## Guarantees the loader provides

| Guarantee | Requirement |
|---|---|
| A template failing any rule above fails at **load time**, naming the file, the template `id`, and the broken rule | — |
| Duplicate `id` anywhere in the library is a load error | — |
| Adding a file requires no code change to become selectable | FR-016 |
| Selection between equally suitable templates is repeatable: candidates sorted by `id`, then rotated by week within the block | FR-020 |
| No matching template raises and names the unsatisfied request, rather than substituting something else | FR-021 |
| Fitting refuses and names the failed constraint rather than emitting an unrideable session | FR-024 |

## What the library does not control

Out of this contract's scope, and unchanged by it: how much training is prescribed and when. Weekly load
targets and phase sequencing come from `app/engine/periodization.py`, which this feature does not touch.
A template describes the *shape* of a session; the periodization decides its *size* and its *place*.

## Adding a template — the check that matters

```bash
uv run pytest tests/test_engine/test_session_library.py -q
```

That suite asserts the library loads, that every `(phase, workout_type)` the periodization can request has
at least one template (FR-019, SC-007), and that selection is deterministic. A contributor who adds a file
and sees it pass has satisfied the contract without reading the generator.
