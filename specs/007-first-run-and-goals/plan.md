# Implementation Plan: First Run, Goals, and Coach Voice

**Branch**: `007-first-run-and-goals` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-first-run-and-goals/spec.md`

## Summary

Turn setup from an interrogation into a confirmation: read everything intervals.icu already knows about the
athlete, show it with its provenance, let them fix what is wrong, and ask only the goal, its date, the
intended volume, and health constraints. Separate changing a goal (keep everything) from starting over
(deliberate, itemised, confirmed). Finish the coach-voice mechanism — which currently *raises for every
persona file* — and make the voice athlete-selectable.

Phase 0 checked the live account and the code ([research.md](./research.md)). Three findings shape the work:

1. **The source has everything the six questions ask for**, except goal, date, and *intended* volume. FTP
   (290), LTHR (182), max HR (202), weight, sex, date of birth are all in `GET /athlete/{id}` — the
   thresholds inside `sportSettings[]` per sport, not the null top-level `icu_ftp`. Age stops being a
   question because `max_hr` is read directly.
2. **`load_persona()` is broken, not just uncalled.** It requires a `ux_prompt` field no `personas/*.yaml`
   has, and the files are English against a French live coach. "Finishing an existing piece" means
   reconciling the schema, rewriting the personas in French to match the live "Pace" voice, and wiring the
   chat path — before anything is selectable.
3. **Goal-change preservation is mostly already true by accident** — `/setup` deletes nothing today. The
   gaps are behavioural: it re-asks everything, there is no distinct `/reset` that actually discards, and
   nothing surfaces stale calendar entries. Spec 005's `check_divergence` already exists for the last one.

**No new outbound write path** unless R3's `sportSettings` write-back probe passes on its own
authorisation. FR-007 (send the athlete to the source, keep no local override) is the shipping default.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: aiogram v3 (the flows), SQLAlchemy async, Pydantic v2, PyYAML (personas),
pytest. No new dependency.

**Storage**: SQLite via SQLAlchemy async. **Three nullable columns, one Alembic revision**:
`users.coach_voice`, `users.disclaimer_acknowledged_at`, and provenance markers folded into the existing
`athlete_profiles.profile` JSON (no migration for the last). No new table.

**Testing**: pytest, plus **real-account validation** of the confirmation flow (SC-001, SC-002, SC-010 are
about a real connected profile) and the `load_persona` regression.

**Target Platform**: Self-hosted single-process Linux container

**Project Type**: Single Python application

**Performance Goals**: Not a factor. One extra `GET /athlete/{id}` per setup; a column read per LLM
request for the voice.

**Constraints**:
- Every readable attribute is read, not asked (FR-001, SC-002)
- Every confirmed value shows its origin and age where known (FR-005, SC-003)
- A correction reaches the source or the athlete is sent there — never a local override (FR-006/FR-007,
  Constitution IV, SC-004)
- A source write-back needs its own explicit approval, recorded (FR-006a/b) — and does not exist until R3
- `/goal` preserves sessions / adherence / conversation entirely (FR-011, SC-005)
- `/reset` is distinct, itemised, confirmed, complete, and issues zero outbound calls (FR-017–020, SC-006/7)
- Voice is athlete state; unresolvable ⇒ safe fallback that says so (FR-024, FR-026)

**Scale/Scope**: One athlete. Two rewritten flows (`/setup`, new `/goal`, new `/reset`), the voice
mechanism finished + `/voice`, one profile reader.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

Constitution is now **v1.1.0** (amended 2026-09-01, spec 006 T059) — the checks below use the current text.

| Principle | Assessment |
|---|---|
| **I. Deterministic engine, zero LLM load calculation** | **PASS.** Plan generation is untouched (spec Scope). The confirmation flow gathers inputs; the engine still computes the plan. The coach voice changes *tone*, never a number. |
| **II. Single-user, local-first** | **PASS.** No new external service. `/reset` deletes only local rows and is explicitly barred from touching the source (FR-020, SC-007) — the plan makes that a grep-verifiable property. |
| **III. Clean layered architecture** | **PASS with a design obligation.** The profile reader is provider-specific → `app/providers/intervals/athlete_profile.py`, a pure mapping with no I/O beyond the client call. The confirmation / goal / reset flows are `app/bot/`. Voice loading is `app/core/persona.py` (exists); the *selection* (column read, fallback) is a thin `app/services/` helper the bot and the LLM path both call — `llm/` must not reach into `persona.py` directly, mirroring how spec 006 kept verification out of `llm/`. |
| **IV. Explicit data provenance, never estimate silently** | **PASS, and central.** This is the principle the whole feature is an application of: read the measured value, show where it came from, never silently default (FR-005/FR-008), and — the sharpest point — never keep a local value that disagrees with the source (FR-007). The new `"source"` value for `ftp_source`/`hr_*_source` is this principle made explicit in the profile JSON. |
| **V. Engine logic is test-covered** | **PASS (narrow).** `app/engine/` is barely touched — only if the level-inference heuristic in `_build_profile` moves. The profile reader gets `tests/test_providers/`; the flows get `tests/test_bot/`; `load_persona` gets `tests/test_core/`. |

### Finding: no new constitutional drift

The v1.1.0 amendment already fixed the `strava/` → `providers/`, PostgreSQL → SQLite, and OAuth → API-key
staleness this feature would otherwise have tripped over. `docs/ARCHITECTURE.md` remains stale (untouched
since scaffold, as noted in specs 005/006) — this feature follows that precedent and updates `CLAUDE.md`
only, which the amended constitution names as the living reference.

## Project Structure

### Documentation (this feature)

```text
specs/007-first-run-and-goals/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/first-run.md
├── checklists/requirements.md   (pre-existing)
└── tasks.md                     (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
app/providers/intervals/
└── athlete_profile.py           # NEW — read_athlete_profile(client) → ReadProfile (pure mapping,
                                 #        sportSettings[] threshold selection, provenance per field)

app/services/
└── coach_voice.py               # NEW — resolve_voice(user) → Persona, with settings.persona default
                                 #        and coach-default fallback (FR-024, FR-026)

app/core/persona.py              # MODIFIED — drop the ux_prompt requirement; a persona is one system_prompt

app/bot/
├── states.py                    # MODIFIED — SetupStates gains CONFIRM_PROFILE, CORRECT_VALUE
├── routers/setup.py             # MODIFIED — read → confirm → correct → ask-only-what's-missing;
│                                #            disclaimer gated on users.disclaimer_acknowledged_at
├── routers/goal.py              # NEW — /goal : silent re-read, ask goal+date, regenerate from current
│                                #        fitness, check_divergence, what-changed summary
├── routers/reset.py             # NEW — /reset : itemised list, typed confirmation, local-only deletes
├── routers/voice.py             # NEW — /voice : list personas, set users.coach_voice
└── setup.py (dispatcher)        # MODIFIED — register goal / reset / voice routers before chat_router

app/db/
├── models/user.py               # MODIFIED — coach_voice, disclaimer_acknowledged_at
└── repositories/user_repo.py    # MODIFIED — set_coach_voice, ack_disclaimer, purge_athlete_data

app/llm/
├── prompts.py                   # MODIFIED — the ~7 inline identities become persona-sourced; _MODE_PERSONA stays
├── tools.py / chat.py           # MODIFIED — COACH_SOUL / build_ux_system_prompt fed by resolve_voice(user)

personas/
├── coach-default.yaml           # REWRITTEN — French, matches the live "Pace" voice (the default)
├── analyste.yaml · zen.yaml     # NEW/REWRITTEN — genuinely different French voices
└── (pace.yaml → folded into coach-default, or kept as an alias)

migrations/versions/             # NEW revision — two columns on users

scripts/athlete_profile.py       # NEW — --describe : the confirmation-screen values + origins (Scenario 0)

tests/
├── test_providers/test_athlete_profile.py   # NEW
├── test_core/test_persona.py                # NEW — the R2 regression: every shipped persona loads
├── test_llm/test_voice_wiring.py            # NEW — resolve_voice feeds the chat path, fallback works
├── test_bot/test_setup_confirm.py           # NEW
├── test_bot/test_goal_change.py             # NEW — row-count preservation (SC-005)
└── test_bot/test_reset.py                   # NEW — typed-confirm gate, completeness, zero outbound calls
```

**Structure Decision**: existing layout. `tests/test_bot/` and `tests/test_core/` are new directories —
there is no bot-router or core test today, and these flows are the first things worth testing at that
level. The judgement call is splitting `coach_voice.py` (selection + fallback) from `persona.py` (file
loading): the loader is a pure cached function, the selector reads a column and knows about `settings` and
the fallback — different concerns, and it keeps `llm/` from importing `persona.py` directly.

## Complexity Tracking

No Constitution Check violations to justify.

## Phase boundaries and known scope limits

Recorded as decisions:

1. **Source write-back is deferred behind its own probe** (research R3). The feature ships with FR-007 as
   the only correction path — direct the athlete to intervals.icu, keep no divergent local value. The
   write-back is added only if a reversible endpoint is verified with explicit authorisation, the way spec
   005 verified the events API. This is called out in the tasks and the eventual commit message.

2. **The voice mechanism is finished for the chat path first.** `build_ux_system_prompt` + `COACH_SOUL`
   (the "Pace" identity, high traffic) move to `resolve_voice(user)` in this feature. The
   narrator / recap / blocks paths (the "Banister" identity) either move in the same change or a tight
   follow-up — but the end state is one selected voice everywhere, never two hard-coded identities.
   `_MODE_PERSONA` (narrative modes) is a different axis and stays untouched.

3. **`/goal` regenerates the plan; it does not preserve the old plan's structure.** FR-012 says "start
   from current fitness", not "adjust the existing plan". A goal change is a new periodisation. Completed
   sessions and adherence are kept as *history* against the now-inactive plan — the same way `/setup`
   already leaves them.

4. **`/reset` deletes; it does not archive.** SC-006 says "discards everything stated once confirmed". No
   soft-delete, no export. The typed confirmation is the safety. `coach_voice` and the disclaimer ack
   survive because they are identity, not training data — a reset athlete should not be re-greeted with
   the disclaimer or lose their chosen voice.

5. **Plan generation is unchanged** (spec Scope, Assumptions). Where this spec's description of the
   end-of-setup generation disagrees with the running code, the code wins and the spec is the defect
   (spec Assumptions, final line).

6. **`docs/ARCHITECTURE.md` stays stale**, consistent with specs 005/006 and the v1.1.0 constitution which
   names `CLAUDE.md` as the living reference. Its rewrite is its own task.
