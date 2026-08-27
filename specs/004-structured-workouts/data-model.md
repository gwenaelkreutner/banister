# Data Model: Structured Workouts and Session Library

**Feature**: 004-structured-workouts | **Date**: 2026-08-27

Entities from [spec.md](./spec.md) §Key Entities, resolved against the existing schemas in
`app/engine/schemas.py`. Nothing here is stored in a database column: plans live as JSON in
`training_plans.plan_technical` and are re-validated on every read, so this model is a Pydantic contract,
not a migration (research R8).

---

## Step

One continuous portion of a session.

| Field | Type | Rule |
|---|---|---|
| `kind` | `Literal["warmup", "work", "recovery", "cooldown", "steady"]` | required |
| `duration_minutes` | `int` | `> 0` — a zero-length step is not a step (edge case: rest days are the *absence* of a session, never a session of zero length) |
| `zone_code` | `str` | must be a key of `TrainingPlanSchema.zones` (`"Z1"`…`"Z6"`) |

**No absolute intensity is ever stored on a step** — no watts, no bpm. This is what makes FR-025 true by
construction: when the athlete's threshold changes, every future session retargets automatically because
nothing absolute was ever persisted, and completed history is untouched because it lives in `session_logs`,
not in the plan.

`steady` exists so an unstructured session (an endurance ride) is the *same kind of thing* as a structured
one — a single `steady` step — rather than a special case (FR-004).

## RepeatGroup

An ordered set of steps performed a stated number of times (FR-003).

| Field | Type | Rule |
|---|---|---|
| `repeat` | `int` | `>= 2`. A group of 1 is not a repetition — it must be expressed as plain steps, so the two representations cannot both encode the same thing |
| `steps` | `list[Step]` | non-empty; the unit that repeats, e.g. `[work 12min Z4, recovery 4min Z1]` |

Groups do not nest. Nothing in the current session vocabulary needs it (research R4), and forbidding it
keeps duration derivation a single pass.

## Structured session — `SessionSpec` (modified)

The existing model gains one optional field. Everything already there stays, with identical meaning
(FR-008).

```python
class SessionSpec(BaseModel):
    # ── existing, unchanged in meaning ──
    day_of_week: int
    workout_type: Literal["long_ride", "intervals", "endurance", "recovery"]
    zone_code: str
    duration_minutes: int
    target_time_in_zone_minutes: int
    tss_target: float
    description_fr: str

    # ── new ──
    steps: list[Step | RepeatGroup] | None = None
```

### Why the summary stays stored rather than becoming computed properties

FR-009 asks for summary attributes "derived from the steps rather than stored independently"; FR-013 asks
that plans stored under the previous shape keep working. Those pull in opposite directions: a legacy
session has no steps, so a purely computed `duration_minutes` would be underivable for exactly the
sessions FR-013 protects.

**Resolution**: the fields remain stored, and a model validator enforces that *when `steps` is present*,
they equal the derivation. Drift becomes a load-time error rather than a silent inconsistency, which is
what FR-009 is actually protecting against, while legacy sessions keep their stored summary verbatim.
This is a deliberate reading of FR-009 in light of FR-013, recorded here rather than resolved silently.

### Derivation rules (applied only when `steps` is present)

| Attribute | Derived as |
|---|---|
| `duration_minutes` | `Σ step.duration_minutes`, where a `RepeatGroup` contributes `repeat × Σ inner durations` |
| `zone_code` | the zone of the `work` steps; for a session whose only step is `steady`, that step's zone |
| `target_time_in_zone_minutes` | total minutes spent in `zone_code` across `work` (or `steady`) steps — matches today's `_target_time_in_zone_minutes()`, which returns `sets × work` for intervals and `0` otherwise |
| `tss_target` | `Σ` per-step load using the existing per-zone intensity factors (`app/engine/tss.py::ZONE_IF` / `ZONE_TSS_PER_HOUR_HR`), so a structured session and today's flat estimate agree in method (FR-006) |

`duration_minutes` derivation replaces `_structure_duration()`'s silent `max(40, min(120, …))` clamp. The
clamp becomes a **fitting constraint that refuses** (FR-024), not an output that quietly disagrees with its
own parts (research R2).

### States

A session is in exactly one of two states, and both are valid:

- **Structured** — `steps is not None`. Summary is derived and validated against the steps.
- **Legacy** — `steps is None`. Summary is trusted as stored. Anything that requires steps reports their
  absence (FR-014); it never fabricates them, because the structure that would be needed was discarded
  when the plan was generated (research R1) and inventing it would be exactly the silent estimation
  Constitution Principle IV forbids.

A plan may hold both at once — the "partially structured plan" edge case is the ordinary state during a
transition and needs no special handling beyond per-session nullability.

---

## SessionTemplate

A reusable pattern in the library, expressed relatively so it fits any athlete (FR-022). Lives in
`sessions/*.yaml`; see [contracts/session-library.md](./contracts/session-library.md) for the authored
format.

| Field | Type | Purpose |
|---|---|---|
| `id` | `str` | stable, unique; used for deterministic selection and for referring to a template in tests |
| `workout_type` | same `Literal` as `SessionSpec` | what the periodization asks for |
| `family` | `str` | finer grain than `workout_type` — `sweet_spot`, `threshold`, `vo2`, `endurance`, `long_ride`, `recovery`, `race` |
| `phases` | `list[Literal["base","build","peak","taper"]]` | which phases may select it (FR-019 coverage is checked against this) |
| `purpose` / `intent` / `suits` | `str` | FR-017 — why this session exists, what it trains, when it fits. Not decoration: this is what makes the library reviewable by someone who understands training but not the code |
| `structure` | `list[Step \| RepeatGroup]` | the pattern itself |
| `scaling` | `ScalingRules` | what fitting may vary, and within what bounds |

## ScalingRules

The bounds within which fitting may adapt a template while "preserving its structural character"
(FR-023). Making the bounds part of the template — rather than a global rule in the fitter — is what lets
a contributor add a template with different elasticity without touching generator logic (FR-016).

| Field | Type | Meaning |
|---|---|---|
| `repeat_range` | `[int, int] \| None` | how many repetitions are acceptable |
| `work_minutes_range` | `[int, int] \| None` | acceptable duration of a single work step |
| `steady_minutes_range` | `[int, int] \| None` | for unstructured sessions, acceptable total |

A template that declares no scaling is fixed and is either selected as-is or not at all.

## SessionLibrary

The loaded collection. Indexed by `(phase, workout_type, family)` for selection.

**Coverage obligation (FR-019, SC-007)**: for every `(phase, workout_type)` the periodization can
request, at least one template must exist. Because `periodization.py` emits only
`(phase, tss_target, is_recovery_week)` and composition happens in `plan_builder._build_week_template()`
(research R4), the full request set is enumerable — so coverage is a test, not an aspiration.

**Selection determinism (FR-020)**: when several templates match, selection uses the existing rotation
`index = week_in_block % len(candidates)` over candidates sorted by `id`. Sorting by `id` is what makes
it repeatable regardless of filesystem ordering; the rotation preserves today's week-to-week variety.

**No match (FR-021)**: raises rather than substituting. A missing template is a library gap the
contributor must see, not a session the athlete silently receives.

## Fitting

Adapting a template to one athlete and one week's load share, producing a `SessionSpec` with steps.

**Input**: template, target load for the session, athlete's `coaching_mode` and available time, plan zones.

**Output**: a `SessionSpec` whose steps sum to its summary — or a **refusal**.

**Refuses when** (FR-024): the load target cannot be met within `ScalingRules` bounds; the fitted session
exceeds the athlete's stated availability; or the result would be unrideable (work intervals below a
meaningful floor). Refusal names which constraint failed — an unrideable session that is silently
produced is worse than a gap the generator can report.

## Relative intensity

Not a new entity — it already exists. `app/engine/schemas.py::Zone` carries both halves (research R5):

```python
lower_pct / upper_pct              # relative — always present
lower_watts / upper_watts          # resolved, power mode
lower_bpm  / upper_bpm             # resolved, HR mode (Karvonen reserve)
```

`TrainingPlanSchema.zones` already stores the resolved dict per plan and `coaching_mode` already
discriminates. So a step referencing `"Z4"` resolves to watts or to bpm at presentation time with no new
machinery — satisfying FR-026 (presented in the athlete's own terms) and FR-027 (no threshold → the zone
is still presentable, absolutes are simply absent rather than invented).

---

## Description and language

`description_fr` is retained on `SessionSpec` for compatibility (FR-008) but stops being the source of
truth. Text is produced from structure by a renderer (FR-028) taking a language, so a stored session no
longer carries single-language text as its *only* description (FR-030).

Two French-locked surfaces exist and are treated differently:

- `plan_builder._session_description()` — replaced by the renderer.
- `Zone.description_fr` in `zones.py` — **left alone by this feature.** It is plan-level, not
  session-level, and touching it pulls in the coach-voice wiring that spec 007 owns (research R6).

End-to-end language switching driven by the configured persona (FR-029) completes in spec 007; this
feature's obligation is that the renderer accepts a language and that nothing in generation logic emits
fixed-language text.
