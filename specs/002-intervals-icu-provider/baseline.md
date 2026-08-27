# Pre-Migration Baseline

**Feature**: 002-intervals-icu-provider | **Captured**: 2026-08-27, before Phase 2 (T004)

These numbers are the regression contract for this feature. Every phase checkpoint compares against them.

## Test suite

```
$ uv run pytest tests/ -q
190 passed, 14 skipped, 15 warnings in 5.95s
```

The 14 skips are the retired PostgreSQL half of spec 003's portability tests (asyncpg removed at cutover),
unrelated to this feature. They remain skipped throughout.

## Lint

```
$ uv run ruff check app/ tests/
Found 269 errors.
```

## Gate for this feature

Per Constitution Principle V and the pragmatic gate established in specs 003: **no new violations beyond
269.** Fixing the pre-existing backlog is out of scope here.

Test count is expected to **change substantially** during this feature — Phase 3 removes tests covering
deleted calculations (T019), Phase 7 removes the manual-entry test surface, and Phases 2–6 add new tests
under `tests/test_providers/` and `tests/test_services/`. The 190/14 figures are the starting point to
diff against at each checkpoint, not a fixed target.
