# Session library

This directory holds every reusable training-session template the plan generator draws from. It is
editable independently of `app/` — adding a session here requires no Python change. See
[`specs/004-structured-workouts/contracts/session-library.md`](../specs/004-structured-workouts/contracts/session-library.md)
for the full, authoritative format contract; this file is the short version.

Mirrors the existing `personas/` convention: contributor-editable content living outside the code that
consumes it.

## Adding a template

1. Pick the right file by `family` (or create a new one — any `sessions/*.yaml` file is loaded).
2. Give it a unique `id`, list the `phases` it may be selected in, and fill in `purpose`, `intent` and
   `suits` — these are required, not decoration. A library nobody can evaluate on training grounds is not
   reviewable, which defeats the point of it being data instead of code.
3. Describe `structure` as an ordered list of steps and repeat groups. Intensity is always a zone code
   (`Z1`–`Z6`) — never watts, never bpm. Absolute targets are resolved per athlete at presentation time.
4. Run the check that matters:

   ```bash
   uv run pytest tests/test_engine/test_session_library.py -q
   ```

   A contributor who adds a file and sees this pass has satisfied the contract without reading the
   generator.

## Minimal example

```yaml
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

## What the library does not control

How much training is prescribed and when — that stays in `app/engine/periodization.py`. A template
describes the *shape* of a session; the periodization decides its *size* and its *place*.
