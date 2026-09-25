# Quickstart Validation: English and French

## Prerequisites

Use a test bot and test data. Set required credentials as in `.env.example`. Preserve existing private `.env`; `APP_LANGUAGE=fr` keeps French on an upgraded installation. Run Python through `uv`.

After editing a `.po` file, run `uv run pybabel compile -d locales -D banister` and include the resulting `.mo` file. To add a language, create `locales/<code>/LC_MESSAGES/banister.po` with every existing key, compile it, then set `APP_LANGUAGE=<code>`.

## Automated gates

1. Run paired locale tests for configuration, catalog parity, command discovery, plan rendering, publication approval, LLM prompts and fallbacks, verification, RPE, notification, and scheduled copy.
2. Run `uv run pytest tests/` and `uv run ruff check app/ tests/`.
3. Audit athlete-facing send/edit paths for French in the English experience and missing translations.

## English walkthrough

1. Omit `APP_LANGUAGE`; start with a fresh owner. Open `/start`, complete `/setup`, open `/help`, and inspect the Telegram command menu. Confirm English and `/fitness` and `/summary` discovery.
2. Use plan navigation, `/fitness`, `/summary`, `/review`, `/goal`, `/voice`, `/reminders`, `/publish`, `/unpublish`, and `/reset` without confirming reset. Exercise buttons and an error path.
3. Ask a chat question, request a workout, record and undo a meal, trigger activity feedback and RPE, and inspect a scheduled reminder. Force one LLM failure.
4. Check that no application-authored French appears. Training numbers, zones, and safety outcomes match the French run.

## French walkthrough

1. Set `APP_LANGUAGE=fr`, restart with the same data, and repeat. Confirm French, `/forme` and `/recap`, and the same coach voice.
2. Inspect an existing French plan and chat history; confirm neither was rewritten.

## Calendar and legacy checks

1. Create an approved publication in French, then restart in English. Existing remote event stays unchanged; an English update requires a fresh preview and approval.
2. View a structured French-era plan in English: display is English, while steps, dates, duration, zones, and TSS are identical.
3. View an old plan without steps: stored description stays verbatim and no structure is invented.
