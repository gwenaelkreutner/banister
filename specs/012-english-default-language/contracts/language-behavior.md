# Language Behavior Contract

## Configuration

| Input | Result |
| --- | --- |
| `APP_LANGUAGE` absent | English (`en`) |
| `APP_LANGUAGE=en` | English |
| `APP_LANGUAGE=fr` | French |
| Any other value | Startup error naming invalid and accepted values |

Language is installation-wide and changes only after restart. Coach voice stays independent.

## Telegram surface

- Native command menu, `/help`, first-run messages, replies, buttons, callback notices, scheduled messages, and activity notifications use the installation language.
- English command discovery uses `/fitness` for `/forme` and `/summary` for `/recap`; old French names stay accepted aliases. Other command names remain stable.
- Callback payloads and stored machine identifiers remain stable; only visible labels change.
- Athlete-authored and source-provided text stays verbatim, including quoted replies, activity names, and saved meal descriptions.

## Coaching and safety

- All LLM calls request the selected language: chat, plan narrative, feedback, recap, review, and fitness commentary.
- Deterministic fallbacks and confirmations use that language.
- Numeric verification recognizes English and French metric terms; same figures produce the same validation result.
- Existing guardrails, calculations, and publication consent remain unchanged.

## Structured sessions and calendar

- A structured session is rendered from its steps in the installation language; `description_fr` remains the persisted compatibility field.
- A legacy session without steps uses stored text verbatim, even if French on an English installation.
- For prospective publication, previewed event name, content hash, and event name sent to intervals.icu are identical in the selected language.
- Restarting with another language never triggers a calendar write. Existing approval and conflict checks govern later publication.
