# Feature Specification: English by Default, French by Choice

**Feature Branch**: none (working in the current checkout)
**Created**: 2026-09-25
**Status**: Draft
**Input**: Make Banister usable in English by default for open-source installations, while preserving the current French experience through a setting in `.env`. The chosen language covers every application-authored text visible to the athlete.

## Scope

**In scope**: English and French for onboarding, help, command discovery, buttons, callback replies, training plans and session descriptions, calendar publication previews and event text, reminders, activity notifications, RPE, chat and tool feedback, weekly recaps, reviews, nutrition confirmations, error and fallback messages, and every other athlete-facing message. The chosen language applies consistently to generated coaching text and deterministic copy. Existing French command names remain usable; English command names are discoverable where a command name itself is French.

**Out of scope**: translating athlete-authored text, activity names or other content received from intervals.icu, previously generated chat messages, and historical unstructured plan descriptions that have no information from which to render another language. Translating repository documentation, logs, and internal developer-facing messages is outside the athlete-facing requirement.

**Depends on**: the existing single-owner, self-hosted deployment model and current English/French structured-session renderer.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a new installation in English (Priority: P1)

A new self-hoster starts Banister without choosing a language. From the first welcome message through setup, daily use, and failure recovery, application-authored text appears in English.

**Why this priority**: English must be the complete default experience for the open-source project.

**Independent Test**: start with a fresh owner and no language setting; exercise onboarding, each command and button path, a plan, chat, an activity notification, a reminder, and a failed language-model call. Inspect every delivered message and calendar preview for application-authored French.

**Acceptance Scenarios**:

1. **Given** a fresh installation with no language setting, **when** the athlete opens `/start`, finishes `/setup`, and asks for `/help`, **then** all application-authored messages, buttons, and available command descriptions are in English.
2. **Given** an English installation, **when** the athlete views a plan, asks the coach a question, logs a meal, reviews an activity, and receives scheduled or activity-triggered messages, **then** labels, explanations, confirmations, and generated responses are in English.
3. **Given** an English installation and a failed or empty language-model response, **when** Banister uses a fallback, **then** the fallback is in English and makes no unsupported training claim.

---

### User Story 2 - Keep a French installation in French (Priority: P1)

The existing owner selects French in `.env` and continues using the current French experience, including the coach's tone and familiar command names, without changing training data.

**Why this priority**: preserving the owner's working French deployment is an explicit requirement.

**Independent Test**: configure French and replay the same representative paths as Story 1 against existing data; compare user-visible behavior and verify that training values and persisted history are unchanged.

**Acceptance Scenarios**:

1. **Given** French is configured, **when** the application restarts, **then** onboarding, commands, buttons, chat, plans, notifications, and fallbacks use French.
2. **Given** French is configured and a French plan already exists, **when** the athlete views or publishes it, **then** the French description is retained and no plan or training metric is regenerated solely because of the language setting.
3. **Given** a coach voice is selected, **when** the installation language changes, **then** the coach keeps that voice's behavioral style while speaking the selected language.

---

### User Story 3 - Keep stored and published content trustworthy (Priority: P2)

An owner can change the installation language on restart without silently rewriting previously approved calendar events, training history, or athlete-authored material.

**Why this priority**: language changes must not bypass calendar approval or alter training facts.

**Independent Test**: create a plan and approved publication in one language, restart in the other, then inspect the plan display, publication preview, existing remote event, and newly approved publication.

**Acceptance Scenarios**:

1. **Given** a structured plan made while French was selected, **when** the owner selects English, **then** its current plan view is readable in English while its training structure, dates, load, and historic record stay the same.
2. **Given** a calendar event was already published, **when** the language setting changes, **then** Banister does not silently overwrite the event; any language-dependent change follows the existing preview and approval rules.
3. **Given** an old plan contains only a stored French free-text description and no session structure, **when** it is viewed in English, **then** Banister preserves that historical text rather than inventing or silently machine-translating a workout.

### Edge Cases

- Invalid language setting: startup reports the invalid value and accepted choices instead of silently choosing another language.
- Missing translation on an athlete-facing path: the English default cannot fall back to French silently; a test must expose the missing text.
- An activity, athlete note, meal description, or quoted reply written in French remains verbatim even on an English installation.
- Established French command names and callback flows continue to work after English names become discoverable.
- A language-model response that mixes languages is treated as a quality failure of that surface; deterministic confirmations remain in the selected language.
- Numbers, zones, units, dates, safety guidance, and verification of training claims keep their existing meaning across languages.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Banister MUST use English when no language is configured and MUST allow the installation owner to select French through a documented `.env` setting.
- **FR-002**: The setting MUST accept only English and French and MUST reject unsupported values with a clear startup error.
- **FR-003**: Every application-authored athlete-facing message and control MUST use the selected language, including onboarding, setup, commands, buttons, callbacks, plan views, notifications, reminders, activity feedback, nutrition records, reviews, recaps, failures, and fallbacks.
- **FR-004**: Every generated coaching path MUST request a response in the selected language while preserving the existing coach style, safety rules, and restrictions on training-load calculation.
- **FR-005**: The selected language MUST be independent of coach voice. Selecting a voice MUST NOT change the installation language, and selecting a language MUST NOT change the voice.
- **FR-006**: New structured session descriptions and language-dependent calendar event text MUST be produced in the selected language from the session's actual training structure, without changing its duration, zones, target load, or steps.
- **FR-007**: Existing French training plans, activity history, messages, and athlete-authored content MUST remain stored as they are. Structured sessions MAY be rendered in the selected language for display; unstructured historical descriptions MUST remain verbatim.
- **FR-008**: A language change MUST NOT by itself modify or replace an already published calendar event. A changed language-dependent event MUST go through the existing preview, approval, and conflict protections before publication.
- **FR-009**: English help and command discovery MUST use English descriptions and provide English aliases for commands whose names are French; existing French command names MUST remain functional.
- **FR-010**: Dates, metric names, and training explanations shown to the athlete MUST be understandable in the selected language, while training values and measurement units retain their original meaning.
- **FR-011**: Response verification and safety checks MUST continue to detect relevant training claims in both supported languages and MUST not weaken existing protections.
- **FR-012**: The owner MUST have clear setup instructions for keeping an upgraded personal deployment in French and for using the English default on a new installation.

### Key Entities

- **Installation language**: one owner-selected choice, English or French, applying to all outgoing application-authored text after restart.
- **Coach voice**: the selected coaching personality and style, independent of installation language.
- **Session description**: athlete-visible wording derived from a structured workout when possible, or preserved historical text when no structure exists.
- **Calendar approval**: the existing consent record binding published workout content to what the athlete previewed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a representative end-to-end walkthrough of every athlete-facing feature, 100% of application-authored text is English with the default setting and French with the French setting, except explicitly preserved historical or external content.
- **SC-002**: The owner can switch between English and French with one documented setting and a restart, without losing any training history, meal history, or coach voice selection.
- **SC-003**: Across both language walkthroughs, training values, session structure, and response-verification outcomes are identical for the same input data.
- **SC-004**: A language change causes zero unapproved calendar writes; any later event change requires the same approval as a training-content change.
- **SC-005**: Every previously supported French command remains usable, and English help exposes English names for the formerly French-only commands.

## Assumptions

- Banister remains a single-owner installation; one setting applies to the entire bot, so no per-athlete language storage or `/language` command is needed.
- The owner will set the French value in their private `.env` before upgrading their live deployment; the committed example and upgrade instructions make this explicit.
- The language of user-provided and external source text is part of that data and is not translated automatically.
- The existing metric units and time zone remain unchanged. Only human-readable wording and date presentation follow the selected language.
- Old unstructured plan text remains in its original language because its exact training meaning cannot be recovered reliably from free text alone.
