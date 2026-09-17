# Phase 0 Research: Freestyle Coaching Mode

Grounded in reading the actual code paths this feature touches, not assumptions — every decision below
cites the file/line that drove it.

## Decision 1: Coaching mode is derived, not stored

**Decision**: "Which mode is the athlete in" is not a new persisted field. It is derived: an active row in
`training_plans` (`plan_repo.get_active_plan()` returns non-None) means goal mode; no active plan means
freestyle mode.

**Rationale**: `training_plans.is_active` already persists across restarts, so FR-007 ("mode survives a
restart") is satisfied for free. Every plan-consuming code path already branches on `plan is None`
(`app/llm/chat.py:50,56,74-80,96`, `app/services/guardrail_service.py:93-95,276-278`) — freestyle mode is
already "the plan-less case" almost everywhere except the two spots identified in Decision 3.

**Alternatives considered**: a new `users.coaching_mode` column (or a `PlanStates.FREESTYLE` FSM state).
Rejected — it would need to be kept in sync with plan activation/deactivation by hand in every place a plan
is created or dropped, duplicating information the `training_plans` table already carries authoritatively.

## Decision 2: Mode switch reuses `/goal`, no new command

**Decision**: Extend `/goal`'s existing choice keyboard (`app/bot/routers/goal.py:28-34`,
`_goal_kb()` — today: Événement cible / Forme générale / Performance / Autre) with a fifth option,
"pas d'objectif / mode libre." Picking it deactivates the current plan (`plan_repo.deactivate_all_for_user`,
already used by `_regenerate()` at `goal.py:122`) instead of generating a new one, and withdraws future
calendar publications (Decision from spec FR-013, reusing the existing `withdraw_all_publications()` used by
`/unpublish`). The reverse direction (freestyle → goal) is `/goal`'s existing flow, unchanged, minus its
current hard block at `goal.py:44` (`if plan is None: "Aucun plan actif — lance /setup."`) — that block is
what makes `/goal` currently unusable as the entry point into goal mode from freestyle, and is the one
required code change to make this reuse work.

**Rationale**: one command already asks "what's your objective"; "I don't have one" is a valid answer to
that same question, not a different concern. Avoids a second command and a second FSM the athlete has to
learn. (Discussed and confirmed with the user before this planning pass.)

**Alternatives considered**: a dedicated `/freestyle` command mirroring `/goal`/`/reset`/`/voice`. Rejected
as unnecessary duplication once the "add an option to `/goal`'s keyboard" path was identified as simpler and
sufficient.

## Decision 3: Post-activity feedback requires a schema change — this is the real cost center

**Decision**: `session_logs.plan_id`, `.week_number`, and `.day_of_week` (`app/db/models/session_log.py:28-36`,
all `nullable=False` today) must become nullable via an Alembic migration. `assemble_activity_feedback`'s
current `no_plan` early return (`app/services/activity_feedback.py:79-81`) must be replaced with a real path
that creates a `SessionLog` with `plan_id=NULL`, skips `evaluate_activity_plan_match` entirely (there is
nothing to match against), and still runs fitness-feedback + highlight selection exactly as the existing
`"bonus"`/`"unplanned"` paths do.

**Rationale**: this was found by reading the code, not inferred from the spec. The comment at
`notifier.py:225-234` is explicit about why activities are silently dropped in the no-plan case today:
*"SessionLog.plan_id is NOT NULL"*. Every other candidate spot for freestyle-mode plan-agnosticism
(`chat.py`, `guardrail_service.py`) already tolerates `plan is None` gracefully — this is the one place that
does not, and it is exactly the code path FR-008/FR-009 and US3 depend on. Treating it as "already mostly
supported" would have been wrong; it is the single largest piece of implementation work this feature
requires.

**Alternatives considered**: creating a synthetic placeholder `TrainingPlan` row per athlete to satisfy the
NOT NULL constraint without a migration. Rejected — it fakes plan ownership data that guardrails and future
features would have to know to ignore, and contradicts Principle IV (explicit provenance: a freestyle ride
did not happen "under" any plan, and pretending otherwise is exactly the kind of silent fiction the
constitution rules out).

## Decision 4: Session selection replaces periodization phase with fitness state

**Decision**: A new deterministic engine function (`app/engine/freestyle_selector.py`, name indicative)
picks `(workout_type, target_tss)` from current fitness (`get_current_fitness()`, same source `/forme`
already uses) and recent training load (`compute_weekly_snapshot`, same one guardrails already use) —
**not** from `session_library.select_template()`'s `phase` argument
(`app/engine/session_library.py:170-192`), which has no meaning without a plan. The resulting
`(workout_type, target_tss)` is fed into the existing, already-generic `fitting.fit_template()`
(`app/engine/fitting.py:213-237`), which takes a template and a target TSS and has never depended on
periodization — it was already phase-agnostic before this feature existed, just never called outside plan
generation (per `CLAUDE.md`: "not yet wired to `generate_plan()`").

**Rationale**: reuses two engine modules (`session_library`, `fitting`) unchanged in their public contract;
the only new logic is "what type of session and how hard, given today's fitness and recent load" — a
genuinely new deterministic rule, not a re-plumbing of existing ones.

**Alternatives considered**: extending `select_template()` to accept fitness metrics instead of `phase`.
Rejected — `select_template` is also called by `plan_builder.py` for real periodized plans; overloading its
signature for a second, unrelated selection strategy would blur what the function means for its existing
caller.

## Decision 5: One new LLM tool, deterministic under the hood

**Decision**: Add one function-calling tool (`TOOL_DEFINITIONS` in `app/llm/tools.py:15`, dispatched in
`app/llm/chat.py`'s `_execute_tool`), e.g. `get_freestyle_session_suggestion`, with no numeric parameters —
it takes no athlete-supplied figures, calls Decision 4's selector plus `fit_template()`, and returns the
result as tool output for the LLM to narrate. The LLM never computes duration/TSS/zone itself (Principle I);
it only phrases the deterministic result and can decline to call the tool if history is insufficient
(FR-011 — the selector returns an explicit "insufficient data" result rather than a guessed session, mirrored
in `recovery_insufficiency()`'s existing pattern in `guardrail_service.py`).

**Rationale**: matches the existing pattern exactly (8 tools already work this way — the model requests,
deterministic Python answers, the model narrates). No new mechanism needed.

## Decision 6: Tool list must be filtered by mode

**Decision**: `TOOL_DEFINITIONS` is passed to every chat call unfiltered today (`chat.py:211`,
`tools=TOOL_DEFINITIONS`). In freestyle mode, the three plan-only tools (`get_upcoming_sessions`,
`propose_plan_modification`, `propose_session_adjustment`) must be excluded from what is offered to the
model, so it is never tempted to call a tool that has nothing to act on. `get_freestyle_session_suggestion`
is excluded symmetrically in goal mode.

**Rationale**: found by reading `chat.py:211` — nothing today varies the tool list by context. Without this,
an athlete in freestyle mode asking something the LLM misreads as a plan-modification request could trigger
a tool call against a plan that does not exist.

**Alternatives considered**: leaving all tools available and having each tool's execution handle `plan is
None` gracefully. Rejected as strictly worse — it lets the model attempt an action that can never succeed in
the current mode, versus never offering it the option.

## Decision 7: Guardrails and coach memory need no changes

**Decision**: No changes to `app/services/guardrail_service.py` or the coach-memory tool/columns.

**Rationale**: verified by reading, not assumed. Both `assemble_workload_findings` and
`assemble_recovery_findings` (`guardrail_service.py:93-95`, `276-278`) already write
`plan_start = plan.start_date if plan is not None else today` — they were already plan-agnostic before this
feature. Coach memory (`AthleteProfile.coach_memory`/`.athlete_notes`) has never been plan-scoped. This
directly satisfies FR-009 and Success Criterion SC-003 with zero code change in that module.

## Decision 8: Activity history keeps flowing regardless of mode already

**Decision**: No change needed to `import_history()` / `ingest_wellness()`.

**Rationale**: `app/providers/intervals/poller.py:196-214` already calls both unconditionally every tick,
independent of whether a plan exists — this is the historical-backfill/wellness path, distinct from the
live per-ride notification path that Decision 3 fixes. Fitness figures (CTL/ATL/TSB) and the `activities`
table are therefore already populated in freestyle mode today; only the live *notification* path was
gated on a plan.

## Open engineering question deliberately left to `/speckit-tasks`

The exact rule for "what workout type and target TSS should freestyle mode suggest, given fitness state X
and recent load Y" (Decision 4) is a genuinely new piece of coaching logic, not a reuse of an existing
formula. This research phase establishes *where* it lives and *what it plugs into*
(`session_library` + `fitting.fit_template()`), not its exact thresholds — those belong in
`data-model.md`'s state-transition sketch and should be finalized as its own task with engine test coverage
per Principle V, the same way `guardrail_thresholds.py` externalizes its thresholds with sources.
