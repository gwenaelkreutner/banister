# Tasks: English by Default, French by Choice

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [language contract](contracts/language-behavior.md)

**Organization**: user stories from the specification. A checked item is implemented and covered by targeted tests; unchecked items remain part of the feature.

## Current handoff (2026-09-25, second update — LLM layer)

The gettext foundation is in `app/core/localization.py` and `locales/{en,fr}/LC_MESSAGES/banister.po`. Compile after every PO edit with `uv run pybabel compile -d locales -D banister`; keep the generated `.mo` files. `APP_LANGUAGE` defaults to `en` and accepts any installed catalog. The owner's existing private `.env` now has `APP_LANGUAGE=fr` to retain the current experience. `t(key, **values)` and `tp(key, count, **values)` are the only helpers for application-authored copy; never add inline `en/fr` pairs. The catalog tests check key/placeholder parity, compiled catalog freshness, plural rules, and adding a third catalog.

**Owner decision (2026-09-25), amends FR-004 — R6 in research.md**: `with_language_rule()` (the hard "OUTPUT LANGUAGE: ..." directive) applies only to one-shot generation paths with no athlete free text to respond to (`/forme`, `/review`, weekly recap coach/next-week sections, plan/week narrative, post-activity feedback, coach-blocks). It does **not** apply to the interactive chat (`app/llm/chat.py`, `app/llm/chat_client.py::run_agentic_loop`) — the model is left free to mirror whatever language the athlete actually writes in. Found and fixed a real pre-existing violation: `run_agentic_loop()` and both LLM providers (`anthropic.py`/`openrouter.py::generate()`) already forced the rule; since the providers apply it to every `generate()` call already, the explicit per-call-site wraps added earlier in this pass (forme.py, narrator.py, activity_analysis.py, review.py, weekly_recap.py) were redundant and have been removed — only `run_agentic_loop` needed the fix (it doesn't go through the provider layer, it uses its own OpenAI client directly). Athlete free-text detectors that scan the athlete's own words (`_ACTION_CLAIM_RE`, `_DECLINE_PHRASES` in chat.py) now match both French and English phrasing, since the athlete may write in either language regardless of `APP_LANGUAGE`.

**The entire LLM layer is now localized** (EN/FR, catalog-key parity verified, 905/905 full suite green): `app/llm/prompts.py` (all system prompts, `WEEKLY_RECAP_*`, `_TID_CLASSIFICATION_FR` → `tid_classification_label()`, `COACH_SOUL`, `build_ux_system_prompt`, `build_narrative_system_prompt`, `build_review_*`, `COACH_BLOCKS_*`), `app/llm/narrator.py` (day/goal/level/phase/workout label functions, reusing existing bot-layer catalog keys where they already existed), `app/llm/review.py`, `app/llm/activity_analysis.py` (all 5 data blocks, fallback texts, match/RPE-mismatch/TSB-tone helpers), `app/llm/tools.py` (all 12 `TOOL_DEFINITIONS` schemas — ~43 description strings — plus `build_system_prompt()`'s entire ~240-line context block: profile, injury, coach memory, fitness, wellness, recent sessions, current week, guardrails), `app/llm/chat_client.py` (the `respond_without_tool` tool schema, text-tool fallback strings), `app/llm/template_picker.py`, `app/llm/prompt_fence.py` (anti-injection fence markers), and `app/llm/chat.py` (all ~20 tool implementations: fitness/session/wellness queries, injury reporting, plan modification proposals, coach memory, freestyle suggestion reasons, the full nutrition tool set including `_format_meal_ledger`, and `memory_query`). Persona files (`personas/*.yaml`) had their hard "réponds exclusivement en français" line removed (R6) — their actual personality text (`system_prompt`/`ux_prompt` bodies) remains French-only content, not yet given English variants; that's a distinct, larger authoring task (see below).

**Known remaining gaps, not started this pass**:
- `app/engine/freestyle_selector.py` — `reasoning_summary`/`duration_warning` text returned by `build_freestyle_suggestion()` flows directly into the chat's freestyle-session tool result and is shown to the athlete; still ~61 French occurrences, untouched.
- Persona bilingual authoring — `personas/*.yaml` `system_prompt`/`ux_prompt` bodies (3 files: coach-default, marseillais, pedagogue) need real English variants, not just mechanical translation (marseillais in particular has a regional voice); requires either a `{language: {en, fr}}` structure in the YAML plus loader changes, or per-language files, and is a creative-writing task as much as a translation one.
- `app/bot/routers/setup.py` (T008) and `goal.py`/`voice.py`/`reminders.py`/`publish.py` (T010) were verified via catalog-parity scan + French-text scan only, not a live walkthrough.
- `app/engine/plan_builder.py`'s per-candidate `detail` flavor text (noted in T014) — still open, same as before.
- Full repo lint (`ruff check app/ tests/`) still has pre-existing findings unrelated to this feature; every file touched this pass is clean of newly introduced issues (verified file-by-file, plus `ruff check --fix` applied for safe import-sorting fixes).

Last full gate: `uv run pytest tests/ -q` → 905 passed, 44 skipped, 0 failed (re-verified after the R6 fix and after the tools.py/chat.py rewrite). T011 and T015 are now effectively done for every file except persona bodies and `freestyle_selector.py` — leave both unchecked below until those two are closed, since "the LLM layer" isn't fully true while they remain French-only.

## Phase 1: Setup

**Purpose**: establish the language contract without changing user-visible behavior.

- [x] T001 Add `APP_LANGUAGE` with `en` default and installed-catalog validation in `app/config.py`; add configuration tests in `tests/test_core/test_localization.py`.
- [x] T002 Document English default and `APP_LANGUAGE=fr` upgrade setting in `.env.example` and `README.md`.

## Phase 2: Foundation

**Purpose**: one language source and complete bilingual catalog before feature paths use it.

- [ ] T003 Add installation language access and a complete English/French text catalog in `app/core/localization.py`; include key/parameter parity tests in `tests/test_core/test_localization.py`.
- [ ] T004 Add a source audit test or script in `tests/test_core/test_localization.py` or `scripts/` that lists athlete-facing send/edit paths still containing untranslated application copy, without flagging developer logs or athlete data.

**Checkpoint**: supported values are validated and translation gaps can be detected.

## Phase 3: User Story 1 — New installation in English (Priority: P1)

**Goal**: every application-authored athlete-facing path uses English by default.

**Independent Test**: start without `APP_LANGUAGE`, complete the [English walkthrough](quickstart.md), and find no application-authored French outside preserved external or historical content.

- [x] T005 [US1] Add English-default first-contact, help, cancellation, and command-menu regression tests in `tests/test_bot/test_common.py` and `tests/test_bot/test_setup.py`. (Delivered as `tests/test_bot/test_common_language.py` (EN/FR parametrized) and `tests/test_bot/test_command_menu.py`.)
- [x] T006 [US1] Localize Telegram command names/descriptions and register `/fitness` and `/summary` while retaining `/forme` and `/recap` aliases in `app/bot/setup.py`, `app/bot/routers/forme.py`, and `app/bot/routers/recap.py`.
- [x] T007 [US1] Localize `/start`, `/help`, and `/cancel` in `app/bot/routers/common.py`.
- [x] T008 [US1] Localize onboarding prompts, corrections, validation, and completion in `app/bot/routers/setup.py` and its keyboards; cover callback branches in `tests/test_bot/test_setup_confirm.py`. (Verified done: no remaining French outside comments/docstrings, repo-wide key-parity audit found zero orphaned `t()`/`tp()` calls.)
- [x] T009 [US1] Localize plan navigation and command errors in `app/bot/routers/plan.py` and `app/bot/keyboards/plan.py`; cover week and overview display in `tests/test_bot/test_plan_language.py`.
- [x] T010 [US1] Localize `/goal`, `/reset`, `/voice`, `/reminders`, and `/publish`/`/unpublish` routers and keyboards in `app/bot/routers/goal.py`, `reset.py`, `voice.py`, `reminders.py`, `publish.py`, and `app/bot/keyboards/publish.py`; add focused callback/error tests under `tests/test_bot/`. (No dedicated test files for `/reminders`/`/publish` routers specifically — covered indirectly by `test_publish_freestyle_unpublish.py`/`test_chat_freestyle_publish.py`; verified by the same repo-wide key-parity audit plus a French-text scan.)
- [ ] T011 [US1] Localize `/review`, `/forme`, `/recap`, chat status/tool trace, meal confirmations, and all deterministic fallbacks in `app/bot/routers/review.py`, `forme.py`, `recap.py`, `chat.py`, `app/llm/review.py`, and `app/llm/chat.py`; add English-path regression tests under `tests/test_bot/` and `tests/test_llm/`. (Code done and passing — every listed file is fully localized. Left unchecked only because no dedicated English-path regression test was added under `tests/test_bot/`/`tests/test_llm/` for this specific surface; existing tests default to `fr` from the repo's `.env` and were only verified not to break.)
- [x] T012 [US1] Localize RPE labels/buttons and activity notifications in `app/engine/rpe.py`, `app/bot/keyboards/session_log.py`, `app/bot/routers/session_log.py`, and `app/providers/intervals/notifier.py`; coordinate with existing RPE edits and add paired tests in `tests/test_engine/test_rpe.py`, `tests/test_bot/test_session_log_rpe.py`, and `tests/test_providers/test_notifier.py`.
- [x] T013 [US1] Localize scheduled reminder, weekly recap, and nutrition reminder text in `app/main.py` and `app/services/weekly_recap.py`; test formatting without running the schedulers in `tests/test_main.py` and `tests/test_services/test_weekly_recap.py`. (No dedicated `tests/test_main.py` exists yet — covered by `tests/test_services/test_weekly_recap.py` plus a manual formatting check; the weekly-recap LLM tone directive/templates stay French-only, deferred to T015.)
- [ ] T014 [US1] Localize new structured session descriptions and all plan/recap/chat display paths in `app/engine/session_render.py`, `app/engine/plan_builder.py`, `app/bot/routers/plan.py`, `app/services/weekly_recap.py`, and `app/llm/tools.py`; test equivalent structure and numbers in `tests/test_engine/test_session_render.py` and `tests/test_engine/test_plan_builder.py`. (Investigated: `session_render.py`/`plan.py` already handle this correctly by design — `description_fr` is intentionally always generated in French (R4) and `render_session_description()` re-derives a localized structural description from `steps` at display time, with special-cased markers for the three race-week templates. Confirmed gap, not yet fixed: `plan_builder.py`'s per-candidate `detail` flavor text — `ENDURANCE_VARIANTS`, "avec portions Z3 optionnelles", the build/peak long_ride col-simulation notes, "3×8min activation" — is French-only and silently dropped (not mistranslated, just absent) when `render_session_description()` re-renders a session in English, because `_session_description()` never forwards `detail` through to the display-time renderer. `app/llm/tools.py` untouched — folded into the LLM-layer pass (T015).)
- [ ] T015 [US1] Supply English and French behavior text for shipped coach personas and make `app/llm/prompts.py`, `app/llm/chat.py`, `app/llm/activity_analysis.py`, `app/llm/narrator.py`, `app/llm/review.py`, and `app/llm/tools.py` select the installation language on every LLM path; test language instructions and fallbacks in `tests/test_llm/`. (All six modules now select the installation language on every one-shot path, per R6's chat exception. Blocking item: persona `system_prompt`/`ux_prompt` bodies in `personas/*.yaml` are still French-only prose — "supply English... behavior text for shipped coach personas" is literally not done yet, it's a creative-writing task, not a mechanical one. Their hard "answer only in French" line was removed so they at least don't fight R6 in the meantime.)
- [x] T016 [US1] Add English metric anchors and localized safe removal text in `app/services/response_verification.py`; test equivalent pass, mismatch, and unretrieved outcomes in `tests/test_llm/test_response_verification.py`.

**Checkpoint**: English is a complete fresh-install experience, with identical training numbers and safeguards.

## Phase 4: User Story 2 — Existing French installation (Priority: P1)

**Goal**: `APP_LANGUAGE=fr` preserves the current French athlete experience and coach style.

**Independent Test**: run the [French walkthrough](quickstart.md) with existing plan and chat data; all application copy is French and stored data is untouched.

- [ ] T017 [US2] Add French configuration and representative full-path regression tests in `tests/test_config.py`, `tests/test_bot/test_common.py`, and `tests/test_llm/test_voice_wiring.py`.
- [ ] T018 [US2] Keep coach voice selection independent of `APP_LANGUAGE` in `app/services/coach_voice.py`, `app/core/persona.py`, and `app/llm/prompts.py`; test the same voice style in both languages in `tests/test_llm/test_voice_wiring.py`.
- [ ] T019 [US2] Preserve French copy and the old `/forme` and `/recap` behavior across localized routers in `app/bot/routers/forme.py`, `recap.py`, and `app/bot/setup.py`; test old aliases in `tests/test_bot/`.
- [ ] T020 [US2] Verify French text and unchanged values for plans, RPE, notifications, scheduled messages, and fallback paths in `tests/test_bot/`, `tests/test_engine/test_rpe.py`, and `tests/test_providers/test_notifier.py`.

**Checkpoint**: configuring French keeps the owner's working experience.

## Phase 5: User Story 3 — Stored and published content (Priority: P2)

**Goal**: switch language without rewriting history or bypassing publication approval.

**Independent Test**: publish under French, restart under English, and confirm no remote write until a new localized preview is approved; inspect structured and legacy plans.

- [ ] T021 [US3] Add tests for structured and unstructured old plans, unchanged stored JSON, and selected-language display in `tests/test_engine/test_session_render.py` and `tests/test_bot/test_plan.py`.
- [ ] T022 [US3] Add approval/hash regression tests for changing language, stale pending approval, and no automatic write in `tests/test_services/test_publication.py` and `tests/test_providers/test_calendar.py`.
- [ ] T023 [US3] Centralize selected-language event naming for preview, content hash, and publication in `app/services/publication.py` and `app/providers/intervals/calendar.py`; keep the intervals.icu DSL grammar in `app/providers/intervals/workout_dsl.py` stable.
- [ ] T024 [US3] Preserve `description_fr` storage while rendering structured current views in the selected language; keep unstructured historical text verbatim in `app/engine/schemas.py`, `app/engine/session_render.py`, and `app/bot/routers/plan.py`.
- [ ] T025 [US3] Verify no stored chat, meal, activity, athlete note, or plan history is rewritten by a locale change in `tests/test_services/test_publication.py` and `tests/test_bot/test_plan.py`.

**Checkpoint**: published content remains consent-bound and historical data remains intact.

## Phase 6: Completion and cross-cutting proof

- [ ] T026 Audit every Telegram send/edit and LLM call path in `app/bot/`, `app/main.py`, `app/providers/intervals/`, `app/services/`, and `app/llm/` against the language contract; close all catalog gaps.
- [ ] T027 Update `CLAUDE.md` with the final language behavior, legacy plan rule, and calendar approval interaction; preserve existing edits in that file.
- [ ] T028 Run targeted regression tests, `uv run pytest tests/`, `uv run ruff check app/ tests/`, and both walkthroughs in `specs/012-english-default-language/quickstart.md`; record concrete results.

## Dependencies and execution order

`T001–T004` precede the user stories. US1 and US2 use the foundation; US2 must be validated against the same localized surfaces as US1. US3 depends on the selected-language session renderer and publication text from US1. T026–T028 follow all three stories.

## Parallel opportunities

After T004, Telegram copy in T006–T013 and LLM prompt work in T015 can proceed on disjoint files, except their shared router and prompt call sites must be integrated sequentially. T016 is independent of Telegram copy. US3 publication tests in T022 can be prepared while UI translation proceeds; T023 waits for the shared session renderer.

## Implementation strategy

Build the foundation, then complete the English fresh-install story. Next verify French parity with the owner's existing data. Finally exercise the language-change/calendar scenario. The first independently demonstrable increment is English from first contact through the common daily paths; the feature is complete only when the full bilingual walkthrough and approval checks pass.
