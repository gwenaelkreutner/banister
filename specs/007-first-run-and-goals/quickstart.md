# Quickstart: Validating First Run, Goals, and Coach Voice

**Feature**: 007-first-run-and-goals | **Date**: 2026-09-01

How to prove this feature works. Each scenario maps to a user story and the success criteria in
[spec.md](./spec.md).

**Mostly validated live**, because the whole point is the first-run experience against a real connected
account. The one thing that needs a probe first is the source write-back (research R3).

---

## Scenario 0 — What the source actually has

```bash
uv run python -m scripts.athlete_profile --describe
```

**Expect**: the confirmation-screen values with their origins — FTP/LTHR/max_hr from `sportSettings`,
weight/sex/RHR/age from the top-level fields, current fitness from `wellness`, recent volume from
`activities`. This is the input to every scenario below. On this account: FTP 290, LTHR 182, max_hr 202,
weight 67, DOB 1990-01-01.

## Scenario 1 — Setup is a confirmation, not an interview (US1, SC-001, SC-002, SC-010)

```bash
uv run pytest tests/test_providers/test_athlete_profile.py -v
```

Then, in Telegram, on a fresh deployment (or after `/reset`): `/setup`.

**Expect**:
- A **confirmation screen** first — the athlete sees FTP, HR figures, weight, fitness, volume *before*
  being asked anything about themselves (FR-002, US1 acceptance 1).
- After `✅ Tout est bon`: **only** goal, date, intended volume, constraints (FR-003).
- **Age is never asked** — `FC max` is read (SC-002). Confirm by counting the questions: one screen + four
  short answers, no more.
- A plan is generated with no further questions (US1 acceptance 3).
- Every input the plan was built from is visible (FR-004) — a `/plan` or a "voici ce sur quoi j'ai
  construit" recap.
- Setup completes in **under 3 minutes** on this account (SC-010) — time it.

## Scenario 2 — Nothing is assumed silently (US2, SC-003, SC-004)

```bash
uv run pytest tests/test_bot/test_setup_confirm.py -v
```

**Expect**:
- Every value on the confirmation screen shows its origin, and its age where the source dates it (SC-003).
- `✏️ Corriger` → pick FTP → enter a new value → the athlete is shown **from what to what** and told the
  value lives at intervals.icu (FR-006a).
- With no verified write-back endpoint: the athlete is directed to change it at the source, and setup
  proceeds on the source's **current** value with the divergence stated — **not** a hidden local override
  (FR-007, SC-004).
- If write-back *is* verified (its own probe passed): approving the write records the approval, and a
  failed write falls through to the FR-007 path (FR-006c).
- A value the source lacks (e.g. no cycling FTP) → the athlete is **asked**, not defaulted (FR-008); the
  coach states it is running on HR (FR-009).
- Thin history → conservative CTL seed, disclosed (FR-010).

**Live check on this account**: `icu_resting_hr` is `65` but the *measured* RHR series ended 2026-07-19.
The confirmation screen must label the 65 as "profil, pas ta FC repos mesurée récente" — not present it as
current.

## Scenario 3 — Changing the goal does not erase the athlete (US3, SC-005)

```bash
uv run pytest tests/test_bot/test_goal_change.py -v
```

In Telegram, on a populated deployment: `/goal`, pick a new event and date.

**Expect**:
- The new plan is built from **current CTL/ATL/TSB**, not zero (FR-012) — check the first week's TSS
  target reflects current fitness.
- `session_logs`, `weekly_adherence`, `chat_messages`, `activities` counts are **identical** before and
  after (SC-005). A row-count assertion, not a glance.
- The athlete is **not re-asked** sport / FTP / age / constraints (FR-013).
- If sessions were published to the calendar under the old plan: the athlete is told the calendar is now
  stale (FR-014) — spec 005's `check_divergence` fires.
- A "what changed / what carried over" summary is shown (FR-016).
- A goal **date in the past** is rejected; a date **< 21 days** or **> 1 year** out is challenged
  (FR-015).

## Scenario 4 — Starting over is possible and deliberate (US4, SC-006, SC-007)

```bash
uv run pytest tests/test_bot/test_reset.py -v
```

In Telegram: `/reset`.

**Expect**:
- A list of **specific counts** — N sessions, M messages, K weeks of adherence, the plan, the profile
  (FR-018). Not "your data".
- Typing anything other than the exact confirmation word → **nothing deleted** (FR-019, SC-006).
- Typing the confirmation word → the deletes are **complete**: every listed row is gone (SC-006), verified
  by re-querying.
- `coach_voice` and `disclaimer_acknowledged_at` **survive** — a subsequent `/setup` does not re-show the
  disclaimer, and the voice is unchanged.
- **intervals.icu is untouched** (FR-020, SC-007): `scripts/athlete_profile --describe` and
  `scripts/calendar_state --describe` show the same content before and after. A `/reset` that issued any
  outbound call is a bug — confirm by grep that the reset path imports no `IntervalsClient` method.

## Scenario 5 — The athlete picks who is coaching them (US5, SC-008)

```bash
uv run pytest tests/test_core/test_persona.py -v
uv run pytest tests/test_llm/test_voice_wiring.py -v
```

In Telegram: `/voice`, pick a different voice, then send any chat message.

**Expect**:
- Every `personas/*.yaml` is listed with a description good enough to choose (FR-022).
- `load_persona()` **loads every shipped file without raising** — research R2 found it currently raises for
  all of them (missing `ux_prompt`). This is the regression check for this scenario.
- The **next** coach message audibly follows the new voice (FR-023) — terse for "Analyste", warm for
  "Zen". No restart.
- Restart the app → the voice is still the selected one (FR-023, SC-008) — it is a column, not memory.
- Past `chat_messages` are unchanged (FR-025).
- Set `users.coach_voice` to a nonsense id → the coach still responds, on `coach-default`, with a one-line
  "(voix introuvable, voix par défaut)" (FR-026).
- Clear the column → the coach uses `settings.persona` (FR-024).

## Scenario 6 — The athlete knows what they are using (US6, SC-009)

**Expect**:
- On a fresh deployment, the disclaimer (`prompts.DISCLAIMER_TEXT`) appears **before** the first coaching
  advice (FR-027, SC-009) — it is the last message of `/setup`.
- `users.disclaimer_acknowledged_at` is set after it is shown.
- A second `/setup`, or any chat message, does **not** repeat it (FR-028) — check the flag gates it.

---

## The regression this feature must not skip

Research R2: `app/core/persona.py::load_persona()` raises `PersonaNotFoundError` for **every** file in
`personas/` because `Persona` requires a `ux_prompt` field none of them have, and the files are English
while the live coach is French.

```bash
uv run pytest tests/test_core/test_persona.py -v
```

**Expect**: after this feature, `load_persona("coach-default")` and every other shipped id return a
`Persona` whose `system_prompt` is French and matches the live "Pace" voice closely enough that the chat
path does not regress. The wiring change (`build_ux_system_prompt` / `COACH_SOUL` → `load_persona`) is
covered by `test_voice_wiring.py`; behaviour parity is checked by reading a few real chat responses before
and after.

---

## Definition of done

- [ ] Scenario 0 run — the confirmation-screen inputs are all real reads, none asked
- [ ] All scenarios pass
- [ ] Setup on the real account: one screen + four answers, under 3 minutes (SC-001, SC-010)
- [ ] Zero readable attributes asked for (SC-002) — counted, not estimated
- [ ] `/goal` preserves 100% of sessions / adherence / conversation — row counts identical (SC-005)
- [ ] `/reset` discards nothing without the typed word, everything with it, and issues zero outbound calls
      (SC-006, SC-007) — the last verified by grep
- [ ] `load_persona()` loads every shipped persona without raising (the R2 regression)
- [ ] A voice change is heard on the next message and survives a restart (SC-008)
- [ ] The disclaimer precedes first advice and is shown once (SC-009)
- [ ] Full suite green; no new lint violations
- [ ] The sportSettings write-back endpoint is either verified-and-used or explicitly deferred to FR-007
      in the commit message (research R3)
