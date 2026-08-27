# Pre-Migration Baseline

**Feature**: 003-local-embedded-database | **Captured**: 2026-08-27, before Phase 2 (T004)

These numbers are the regression contract for this feature. Every phase checkpoint compares against them.

## Test suite

```
$ uv run pytest tests/ -q
164 passed, 15 warnings in 6.93s
```

## Lint

```
$ uv run ruff check app/ tests/
Found 287 errors.
[*] 75 fixable with the `--fix` option (9 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

**Note**: `plan.md` recorded 291 violations when it was written. The current count is 287 — a small drift
from intervening fixes (the `app/db/models/__init__.py` and `app/db/repositories/__init__.py` repairs
earlier in this session), not from anything in this feature. **287 is the authoritative number this
feature is judged against**; 291 in the plan is now stale and should be treated as historical context, not
as the gate.

## Gate for this feature

Per Constitution Principle V and the plan's adopted pragmatic gate: **no new violations beyond 287.**
Fixing the pre-existing 287 is out of scope here — it is a separate cleanup effort.
