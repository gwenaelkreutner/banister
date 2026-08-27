# Behaviour baseline — captured before spec 004 touches `app/engine/`

Captured 2026-08-28, before any change to `SessionSpec` or `plan_builder.py`. This is the "before" that
`behaviour.json` and later `after.json` are diffed against for SC-004 (spec 004 T002, T022).

## Counts

```
uv run pytest tests/ -q       → 237 passed, 1 failed, 13 skipped
uv run ruff check app/ tests/ → 226 violations
```

**The gate for T054 is "no new failures/violations beyond this baseline"**, not zero — see the caveat
below for why one pre-existing failure is included deliberately rather than fixed first.

## Caveat: `test_sessions_on_available_days_only` is a pre-existing, date-triggered flake

Failing at capture time:

```
tests/test_engine/test_plan_builder.py::test_sessions_on_available_days_only
AssertionError: Séance sur jour non disponible: 4
```

**Not caused by anything in this feature** — `git status` was clean before this snapshot was taken, and
no code in `app/` has been touched yet. The proximate cause is the environment's wall-clock date changing
from 2026-08-27 to 2026-08-28 between sessions: `generate_plan()`'s race week lands on the athlete's fixed
event date regardless of which weekdays the athlete made available, and the test asserts every session —
race day included — falls on an available day. That assumption happens to hold or not hold depending on
which weekday `date.today()` puts the race on, which is why this test previously passed and now
intermittently doesn't.

This is a real, pre-existing defect in test coverage (the test should exempt the race-day marker session,
not assert it lands on an available day), but it is **out of scope for spec 004** — fixing it is unrelated
to structured workouts and touches test assumptions this feature does not own. Recorded here so:

1. It is not misattributed to spec 004 work later.
2. The T054 lint/test gate is evaluated against "237 passed, 1 known-unrelated failure, 226 lint
   violations", not silently against a clean run that does not actually exist at this commit.

If this becomes a recurring flake (rather than a one-time date-crossing artifact), it should be raised and
fixed as its own small, separate change — not folded into this feature's diff.

## What `behaviour.json` actually covers

The real database currently holds 5 real `session_logs`, **all status `"unplanned"`** — none matched to a
plan slot (consistent with what spec 002's live testing found: the athlete's actual ride days didn't align
with the generated plan's specific slots). `behaviour.json` therefore exercises the **matching decision**
(`evaluate_activity_plan_match`) for all 5 — confirming none finds a candidate, none reports
`all_slots_taken` — but does **not** exercise `compute_session_kpi` against a real matched activity, since
no `"done"` log exists to score.

This is a real limitation of the available corpus, not a gap in the snapshot script: the script also
records KPI results for any log that *is* `"done"` and matched, so the moment a real ride matches a plan
slot, re-running it will start covering that path too. The synthetic, richer test coverage for the KPI
path lives in the test suite itself (`tests/test_engine/test_adherence_kpi.py`,
`tests/test_analysis/test_matching.py`), which is not corpus-limited.
