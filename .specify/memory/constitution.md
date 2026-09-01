<!--
Sync Impact Report
Version change: 1.0.0 → 1.1.0
Ratified: 2026-08-27 | Last amended: 2026-09-01

Rationale: the document had drifted from the codebase across specs 002–006 and was, by
spec 006, being *cited* by a specification (FR-022a → Principle V → `tests/test_strava/`,
a directory deleted in spec 002). This amendment aligns the text with the shipped
architecture and adds one materially new guarantee (response verification).

Modified principles:
  - I. Deterministic Engine — extended: the LLM's numeric *claims* about load are now
    verified against retrieved values before delivery (spec 006 US3). Principle unchanged
    in intent; scope of the guarantee widened, hence MINOR not PATCH.
  - III. Clean Layered Architecture — `strava/` → `providers/`; the fetch→analyze→match
    pipeline description generalised to the provider abstraction that replaced Strava.
  - V. Engine Logic Is Test-Covered — `tests/test_strava/` → `tests/test_providers/`;
    added `guardrails`, `baselines`, `session_library`, `fitting` to the named modules.

Modified sections:
  - Technology & Security Constraints — PostgreSQL/asyncpg/Supabase → SQLite/aiosqlite
    (spec 003); Strava OAuth → intervals.icu personal API key, polling only (specs
    001/002); the events API is now an outbound write path (spec 005).
  - Development Workflow — manual Supabase migrations / `init.sql` → Alembic applied
    automatically at startup (spec 003).

Removed: none. Governance section unchanged.

No MAJOR bump: Principles I and II are not redefined. The deterministic-engine and
single-user/local-first guarantees are strengthened by this amendment, not weakened.
-->

# Banister Constitution

## Core Principles

### I. Deterministic Engine, Zero LLM Load Calculation (NON-NEGOTIABLE)
The LLM MUST NEVER compute training load. All quantitative calculations — TSS/HRSS, power
and heart-rate zones, ATL/CTL/TSB, periodization, plan structure, adherence KPI, guardrail
signals — MUST be produced by pure, deterministic Python in `app/engine/` (and equivalent
domain logic), with zero calls to an LLM provider anywhere in that code path. The LLM's
role is strictly narration, coaching tone, and conversational adaptation over numbers the
engine already computed.

Any numeric value the LLM states about the athlete's training MUST additionally be
verified against the value that was actually retrieved or computed, before the response
reaches the athlete; a stated value that disagrees with the data, or a value stated for a
metric that was never retrieved, MUST be withheld and the failure recorded (spec 006 US3).
This verification is itself deterministic — the LLM never checks its own output.

Rationale: training load numbers drive real athletic decisions (overtraining, injury
risk). Non-deterministic generation of these numbers is unacceptable, and a coach that
occasionally invents a figure is worse than one that says less, because the athlete cannot
tell which numbers to trust. Determinism also makes the engine independently unit-testable
and auditable.

### II. Single-User, Local-First, No Cloud Dependency
Banister runs self-hosted for a single owner. The Telegram owner guard (`TELEGRAM_OWNER_ID`)
MUST be enforced at the middleware level so messages from any other Telegram user are
silently dropped. The system MUST remain fully operable without any mandatory external
cloud service beyond the LLM provider and the intervals.icu training-data integration — no
multi-tenant assumptions, no hosted-SaaS coupling. Secrets (`.env`, API keys, tokens) MUST
NOT be committed.

Rationale: this is a personal training tool, not a multi-tenant product; the threat model,
scaling model, and operational simplicity all follow from single-user local-first design.

### III. Clean Layered Architecture
Responsibilities stay separated by layer: `bot/` (aiogram routers, keyboards, FSM states)
talks to `db/repositories/` for persistence and to `services/`/`engine/` for logic —
handlers MUST NOT contain raw SQL or embed engine math directly. `engine/` MUST NOT import
aiogram or any bot/LLM concern. `llm/` MUST NOT perform calculations, only read
pre-computed context and generate text; anything that *evaluates* the LLM's output (e.g.
response verification) is a service the chat orchestrator calls, not a step inside `llm/`.
Training-data ingestion lives behind the provider abstraction in `app/providers/`
(intervals.icu today; any future provider mirrors its module boundary), isolated behind a
fetch → analyze → match pipeline and decoupled from bot and engine internals. Provider
modules that perform outbound writes to the athlete's account (e.g. calendar publication)
MUST keep those writes free of database access — persistence and consent are the service
layer's job.

Rationale: this separation is what keeps the deterministic-engine guarantee (Principle I)
enforceable and keeps the codebase navigable as integrations change.

### IV. Explicit Data Provenance, Never Estimate Silently
Wherever a real measurement is available, it MUST be preferred over a computed or estimated
value, and the choice MUST be recorded, not hidden: metrics the source already computes
(TSS, CTL/ATL/TSB, power/HR zones, FTP/LTHR, CTL ramp rate) are consumed as-is and never
recomputed locally; `ftp_source`/`hr_max_source` are tracked as `"declared"` or
`"estimated"` (never silently attributed to the source); TSS method is persisted alongside
the value. Missing data MUST be treated as unknown — never substituted with a normal or
default value, and never inferred from a stale baseline when the current reading is absent.
Silent fallback to a guessed number without recording that it was guessed is a defect.

Rationale: coaching decisions and trend analysis are only meaningful if we know whether a
number is measured, inferred, or absent; conflating these silently erodes trust in every
downstream calculation, and a guardrail that fires on a substituted value is worse than
one that stays silent.

### V. Engine Logic Is Test-Covered
Any change to `app/engine/` (zones, TSS/HRSS, periodization, plan_builder, plan_modifier,
atl_ctl, weekly_snapshot, adherence_kpi, session_library, fitting, guardrails, baselines,
guardrail_thresholds) or to the provider analysis/matching pipeline MUST ship with
corresponding tests under `tests/test_engine/` or `tests/test_providers/`. Threshold logic
MUST be tested including the cases where a threshold must NOT fire (insufficient history,
a single anomalous reading, values within range). `pytest tests/` and
`ruff check app/ tests/` MUST pass before a change in these areas is considered done.

Rationale: this logic is the non-negotiable deterministic core (Principle I); regressions
here are silent and produce plausible-looking but wrong training guidance, so tests are
the only practical guardrail.

## Technology & Security Constraints

Runtime is Python 3.13 with aiogram v3 (bot) + FastAPI (Telegram webhook) in a single
process. The database is a local SQLite file (one file under `DATA_DIR`) accessed via
SQLAlchemy async + `aiosqlite`; there is no separate database service to administer, and
the file is created and migrated automatically at startup. LLM access goes through the
provider abstraction in `app/llm/providers/` (OpenRouter and/or Anthropic) — new providers
MUST implement the existing common interface rather than being special-cased into
handlers. The intervals.icu integration authenticates with a personal API key (HTTP basic
auth), polls for new activities (no inbound webhook, no registered OAuth application), and
MAY write to the athlete's calendar only through a recorded, content-bound approval. Any
new external integration MUST be added behind its own module boundary under
`app/providers/`, never inlined into bot routers.

## Development Workflow

Schema changes ship as Alembic revisions under `migrations/versions/`, generated with
`alembic revision --autogenerate` and applied automatically at startup by
`app/db/lifecycle.py::run_migrations()` — never a hand-edited SQL file, never a silent
edit of a prior revision. `CLAUDE.md` is the living operational reference (architecture
map, navigation table, business rules) and MUST be updated in the same change when a new
module, table, or non-obvious business rule is introduced. Router registration order in
`app/bot/setup.py` is significant (`chat_router` last, catch-all) and MUST be preserved
unless the change is explicitly about reordering it. Lint (`ruff check app/ tests/`) and
the relevant test subset MUST pass before a change is considered complete.

## Governance

This constitution supersedes ad hoc practice for anything it covers. Amendments are made
by editing this file, bumping the version per semantic versioning (MAJOR: backward
incompatible principle removal/redefinition; MINOR: new principle or materially expanded
guarantee; PATCH: clarification/wording), updating **Last Amended**, and recording the
change in this file's Sync Impact Report. Principles I and II are non-negotiable in
intent even where not explicitly tagged as such — changing them requires a MAJOR bump and
an explicit rationale in the Sync Impact Report. Reviews of non-trivial changes (new
modules, new integrations, engine changes) SHOULD verify compliance with the Core
Principles above; `CLAUDE.md` carries day-to-day operational detail that must stay
consistent with, but does not override, this document.

**Version**: 1.1.0 | **Ratified**: 2026-08-27 | **Last Amended**: 2026-09-01
