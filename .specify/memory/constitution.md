<!--
Sync Impact Report
Version change: (unratified template) → 1.0.0
Modified principles: n/a (initial ratification)
Added sections:
  - Core Principles: I. Deterministic Engine, Zero LLM Load Calculation (NON-NEGOTIABLE);
    II. Single-User, Local-First, No Cloud Dependency; III. Clean Layered Architecture;
    IV. Explicit Data Provenance, Never Estimate Silently; V. Engine Logic Is Test-Covered
  - Technology & Security Constraints
  - Development Workflow
  - Governance
Removed sections: none (all placeholders replaced)
Deferred items: none — RATIFICATION_DATE set to date of initial ratification since no prior
  formal constitution existed for this project.
-->

# Banister Constitution

## Core Principles

### I. Deterministic Engine, Zero LLM Load Calculation (NON-NEGOTIABLE)
The LLM MUST NEVER compute training load. All quantitative calculations — TSS/HRSS, power
and heart-rate zones, ATL/CTL/TSB, periodization, plan structure, adherence KPI — MUST be
produced by pure, deterministic Python in `app/engine/` (and equivalent domain logic), with
zero calls to an LLM provider anywhere in that code path. The LLM's role is strictly narration,
coaching tone, and conversational adaptation over numbers the engine already computed.
Rationale: training load numbers drive real athletic decisions (overtraining, injury risk).
Non-deterministic generation of these numbers is unacceptable; determinism also makes the
engine independently unit-testable and auditable.

### II. Single-User, Local-First, No Cloud Dependency
Banister runs self-hosted for a single owner. The Telegram owner guard (`TELEGRAM_OWNER_ID`)
MUST be enforced at the middleware level so messages from any other Telegram user are silently
dropped. The system MUST remain fully operable without any mandatory external cloud service
beyond the LLM provider and the optional Strava/sport-data integration — no multi-tenant
assumptions, no hosted-SaaS coupling. Secrets (`.env`, API keys, tokens) MUST NOT be committed.
Rationale: this is a personal training tool, not a multi-tenant product; the threat model,
scaling model, and operational simplicity all follow from single-user local-first design.

### III. Clean Layered Architecture
Responsibilities stay separated by layer: `bot/` (aiogram routers, keyboards, FSM states) talks
to `db/repositories/` for persistence and to `services/`/`engine/` for logic — handlers MUST NOT
contain raw SQL or embed engine math directly. `engine/` MUST NOT import aiogram or any bot/LLM
concern. `llm/` MUST NOT perform calculations, only read pre-computed context and generate text.
Sport-data ingestion (`strava/` and any future provider) stays isolated behind its own
fetch → analyze → match pipeline, decoupled from bot and engine internals.
Rationale: this separation is what keeps the deterministic-engine guarantee (Principle I)
enforceable and keeps the codebase navigable as integrations (Strava, future providers) change.

### IV. Explicit Data Provenance, Never Estimate Silently
Wherever a real measurement is available, it MUST be preferred over a computed or estimated
value, and the choice MUST be recorded, not hidden: e.g. NP priority (Strava weighted average >
computed from streams > `None`, never estimated), `ftp_source`/`hr_max_source` tracked as
`"declared"` or `"estimated"` (never silently `"strava"`), TSS method (`"power"` | `"hr"` |
`"estimation"`) persisted alongside the value. Silent fallback to a guessed number without
recording that it was guessed is a defect.
Rationale: coaching decisions and trend analysis (ATL/CTL/TSB, adherence) are only meaningful if
we know whether a number is measured or inferred; conflating the two silently erodes trust in
every downstream calculation.

### V. Engine Logic Is Test-Covered
Any change to `app/engine/` (zones, TSS/HRSS, periodization, plan_builder, atl_ctl,
weekly_snapshot, adherence_kpi) or to the Strava analysis/matching pipeline MUST ship with
corresponding tests under `tests/test_engine/` or `tests/test_strava/`. `pytest tests/` and
`ruff check app/ tests/` MUST pass before a change in these areas is considered done.
Rationale: this logic is the non-negotiable deterministic core (Principle I); regressions here
are silent and produce plausible-looking but wrong training guidance, so tests are the only
practical guardrail.

## Technology & Security Constraints

Runtime is Python 3.13 with aiogram v3 (bot) + FastAPI (webhooks/OAuth) in a single process.
Database is PostgreSQL (Supabase-hosted or local Docker) accessed via SQLAlchemy async +
asyncpg; connections MUST use SSL where the deployment target requires it. LLM access goes
through the provider abstraction in `app/llm/providers/` (OpenRouter and/or Anthropic) — new
providers MUST implement the existing common interface rather than being special-cased into
handlers. Strava OAuth state MUST remain HMAC-signed and verified. Any new external integration
MUST be added behind its own module boundary (mirroring `strava/`), never inlined into bot
routers.

## Development Workflow

Migrations live under `migrations/` and are applied manually (Supabase SQL editor) or via
`migrations/init.sql` for local Docker bootstrap — a schema change MUST ship with a new
numbered migration file, never a silent hand-edit of a prior one. `CLAUDE.md` is the living
operational reference (architecture map, navigation table, business rules) and MUST be updated
in the same change when a new module, table, or non-obvious business rule is introduced — see
the project's own documentation-update policy. Router registration order in
`app/bot/setup.py` is significant (`chat_router` last, catch-all) and MUST be preserved unless
the change is explicitly about reordering it. Lint (`ruff check app/ tests/`) and the relevant
test subset MUST pass before a change is considered complete.

## Governance

This constitution supersedes ad hoc practice for anything it covers. Amendments are made by
editing this file, bumping `CONSTITUTION_VERSION` per semantic versioning (MAJOR: backward
incompatible principle removal/redefinition; MINOR: new principle or materially expanded
guidance; PATCH: clarification/wording), and updating `LAST_AMENDED_DATE`. Principles I and II
are marked non-negotiable in intent even where not explicitly tagged as such — changing them
requires a MAJOR bump and an explicit rationale recorded in this file's Sync Impact Report.
Reviews of non-trivial changes (new modules, new integrations, engine changes) SHOULD verify
compliance with the Core Principles above; `CLAUDE.md` carries day-to-day operational detail
that must stay consistent with, but does not override, this document.

**Version**: 1.0.0 | **Ratified**: 2026-08-27 | **Last Amended**: 2026-08-27
