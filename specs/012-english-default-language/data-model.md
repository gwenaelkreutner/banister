# Data Model: Installation Language

## InstallationLanguage

An owner-selected enum `en` or `fr`. Loaded at startup from `APP_LANGUAGE`; absent means `en`, other values fail validation. It is not stored in the athlete database. Changing it requires restart.

## LocalizedText

Application-authored copy has a stable meaning/key and English/French variants. Parameters remain data values. Both variants accept the same required parameters. Missing variants are a development failure, never a silent fallback to the other language.

## CoachVoice

The existing persona identifier determines identity and style, independently of language. Its stored value is unchanged. Presentation rules for that persona exist in both languages; old persona `language` metadata cannot override installation language.

## SessionDescription

`SessionSpec` continues to store `description_fr` for compatibility. If `steps` exist, display text comes from `workout_type`, `steps`, `coaching_mode`, and language; optional narrative detail is localized too. Without steps, historical text appears verbatim.

## CalendarApproval

Existing approval remains bound to the exact previewed content. The localized event name participates in that content. An approval prepared in one language is stale when the prospective event name changes in another. Existing published entries and events remain untouched until separately approved publication or withdrawal.

## Data preservation

No database migration is planned. Chat history, activity names, meal descriptions, athlete notes, and old plans remain byte-for-byte. Training values and machine statuses are language-neutral.
