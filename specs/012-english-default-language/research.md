# Research: English by Default, French by Choice

## R1 — Installation language

**Decision**: use one validated `APP_LANGUAGE` value (`en` or `fr`), default `en`; make it available to handlers and background jobs, and pass it explicitly to pure renderers.

**Rationale**: `app/config.py` already reads `.env` through Pydantic Settings. The constitution specifies a single-owner installation, and scheduled messages in `app/main.py` lack a Telegram request context. The owner chose `.env` over `/language`.

**Alternatives considered**: per-user database storage requires a migration and second state source; Telegram user locale would override the owner's explicit choice.

## R2 — Text ownership and coverage

**Decision**: use a small English/French catalog for shared deterministic copy, with localized formatters in owning features. Require key parity and audit every athlete-facing send/edit path, including errors and callbacks.

**Rationale**: `app/bot/setup.py` has French command descriptions, `app/bot/routers/common.py` has separate French help, and about 212 send/edit calls span routers, jobs, and the intervals notifier. A chat prompt cannot localize them.

**Alternatives considered**: global replacement risks changing internal values; runtime translation adds network cost and may alter training meaning.

## R3 — Coach voice and LLM language

**Decision**: keep voice selection as style and supply installation language to every LLM path. Provide both languages for shipped persona behavior, shared safety rules, and deterministic fallbacks.

**Rationale**: `resolve_voice()` selects by `users.coach_voice` or `settings.persona`, while shipped personas and `app/llm/prompts.py` explicitly say French. Voice cannot be the language switch.

**Alternatives considered**: selecting another persona for English changes personality and leaves other LLM calls in French.

## R4 — Persisted plans and calendar content

**Decision**: preserve `description_fr`; derive selected-language text from steps for current display, chat context, preview, and event name. Keep unstructured historic text verbatim. Preview, hash, and publish must use the same localized name; a setting change alone writes nothing.

**Rationale**: `SessionSpec.description_fr` is persisted; `session_render.render_description()` supports both languages but defaults French. Plan, recap, chat, publication, and calendar read `description_fr` directly. Publication hashes include the name.

**Alternatives considered**: rewriting plan JSON mutates history; storing a second description duplicates derivable data; LLM translation of legacy text can invent workout details.

## R5 — Provider syntax and safety verification

**Decision**: localize athlete-facing event names while preserving the existing workout DSL grammar unless its provider contract is verified separately. Extend metric anchors and safe replacement wording to English without changing numeric tolerance.

**Rationale**: `render_dsl()` uses English headers as intervals.icu syntax. `response_verification.py` has French anchors such as `fc de repos`, `variabilité cardiaque`, and `monotonie`; English claims could bypass checks.

**Alternatives considered**: translating provider syntax as UI risks incompatibility; retaining French-only anchors weakens verification in English mode.

## R6 — Chat language: mirror the athlete, don't impose a rule (added 2026-09-25, owner decision)

**Decision**: `with_language_rule()` (the hard "OUTPUT LANGUAGE: ..." system-prompt directive) applies only to one-shot generation paths that have no athlete free text to respond to — `/forme`'s interpretation, `/review`'s synthesis, the weekly recap's coach/next-week sections, the plan narrative, post-activity feedback, and any scheduled/notification generation. It is **not** applied to the interactive chat (`app/llm/chat.py`, `app/llm/chat_client.py`, `build_system_prompt()`). There, the model is left free to mirror whatever language the athlete actually writes in, which is the natural and expected behavior for a conversational LLM — forcing a fixed output language there would fight that instead of helping it. Deterministic context labels injected into the chat's system prompt (CTL/TSB, zone names, workout-type names, etc.) still follow `APP_LANGUAGE`, since that's the self-hoster's configured default framing; only the *final reply's* language is left unconstrained for chat specifically.

**Rationale**: revises FR-004's literal "every generated coaching path MUST request a response in the selected language" — that rule stands for non-conversational paths, but a chat that ignores what the athlete actually typed in favor of an installation-wide setting is worse UX than the problem FR-004 was solving (a French-only coach on an English-default install). Not yet extended to the rest of the app (deterministic Telegram text stays strictly `APP_LANGUAGE`-driven); flagged as a evolution to revisit later, not a general principle applied everywhere yet.

**Alternatives considered**: applying the rule everywhere (current spec text, rejected by owner — see conversation 2026-09-25); detecting the athlete's language from their message and switching `APP_LANGUAGE`-driven UI to match (rejected — single-owner instance, more state than needed, and Telegram UI elements like buttons can't dynamically retranslate per message anyway).
