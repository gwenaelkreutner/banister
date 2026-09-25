# Implementation Plan: English by Default, French by Choice

**Branch**: current checkout | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/012-english-default-language/spec.md`

## Summary

Add one validated installation language, default English and selectable French in `.env`. Apply it to deterministic Telegram copy, structured workout rendering, calendar previews and event names, and all LLM prompts and fallbacks. Keep coach voice independent, preserve stored French history, and reuse calendar approval. Finish with an English and French walkthrough of every athlete-facing feature.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: aiogram v3, FastAPI, Pydantic Settings, SQLAlchemy, existing LLM abstraction; Babel for catalog compilation during development

**Storage**: SQLite; no migration expected. Existing plans contain `description_fr` and optional structured `steps`.

**Testing**: `uv run pytest` targeted and full suites; `uv run ruff check app/ tests/`; paired English/French coverage.

**Target Platform**: single-owner self-hosted Telegram bot, usually Docker on Linux; development on Windows.

**Project Type**: one Python service with Telegram bot and background jobs.

**Performance Goals**: no remote translation call and no per-message language lookup in the database.

**Constraints**: training calculations and stored history unchanged; no unapproved calendar write; French commands keep working; no mandatory new service.

**Scale/Scope**: one owner per installation; about 90 application modules and over 200 Telegram send/edit call sites need an audit.

## Constitution Check

*Gate: passed before research and after design.*

- **I. Deterministic engine**: language changes presentation only. Extend English metric anchors in response verification; retain numeric rules.
- **II. Single-user, local-first**: one `.env` value suffices; no database preference or translation service.
- **III. Layering**: bot owns Telegram text; LLM owns prompt wording; services own approval; engine owns pure description rendering. Shared localization code imports no higher layer.
- **IV. Provenance**: preserve historical free text that cannot be reconstructed; never invent a workout translation.
- **V. Tests**: pair engine/provider changes with regression tests; run focused and full/lint gates.
- **Workflow**: update `CLAUDE.md` as code lands; preserve router order and existing worktree edits.

## Project Structure

### Documentation (this feature)

```text
specs/012-english-default-language/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/language-behavior.md
├── checklists/requirements.md
└── tasks.md                 # tasks phase
```

### Source Code (repository root)

```text
app/
├── config.py                 # validated installation language
├── core/                     # shared language/catalog, no bot or LLM imports
├── bot/setup.py              # localized command menu
├── bot/routers/              # command/callback/fallback text
├── bot/keyboards/            # button labels
├── engine/session_render.py  # structured description by language
├── llm/                     # prompts, context, fallbacks
├── services/publication.py  # preview/hash/approval use the same name
├── services/response_verification.py  # English/French metric anchors
├── providers/intervals/     # notification and event names
└── main.py                  # scheduled messages
personas/                    # style available in both languages
tests/                       # paired locale tests
```

**Structure Decision**: use gettext catalogs under `locales/<language>/LC_MESSAGES/banister.po`, compiled to `.mo` for runtime. Every application-authored visible string gets a stable key; feature code supplies only keys and dynamic values. Runtime uses Python's standard-library `gettext`, with no translation service or runtime Babel dependency. `APP_LANGUAGE` accepts any installed catalog, so a new language needs a complete catalog and no new branch in application code. Pass language explicitly to pure renderers and formatters.

## Delivery Order

1. Validated setting, gettext catalog contract, parity and placeholder tests, and French upgrade instructions.
2. First contact, setup, command discovery, menus, callbacks, and deterministic failures. Preserve French command aliases.
3. Plan display, recaps, reminders, RPE, notifications, calendar preview/event naming. Preserve `description_fr` and legacy text; test approval after locale changes.
4. All LLM paths: chat, tool traces/results, plan narrative, activity feedback, recap, review, fitness, nutrition, personas, fallbacks. Add English response-verification anchors.
5. Full English/French quickstart, exhaustive send/edit audit, required tests/lint, and `CLAUDE.md` update.

## Complexity Tracking

No constitution violation is required. The breadth reflects existing product surfaces; it does not justify a translation service, database field, or new user mode.
