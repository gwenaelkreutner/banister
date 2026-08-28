# Quickstart: Validating Calendar Publication

**Feature**: 005-planned-workout-push | **Date**: 2026-08-28

How to prove this feature works. Each scenario maps to a user story and the success criteria in
[spec.md](./spec.md).

**This feature writes to the athlete's real account.** That is not incidental — research R2 (the API does
*not* upsert; re-POSTing duplicates) is precisely the kind of finding no mock produces, and the guarantees
here are about a remote service's actual behaviour. Write-testing was explicitly authorized before any
write occurred.

**The discipline that makes that safe**, and which every scenario below follows:

- Everything this system writes carries the `banister:` prefix. Nothing else is ever touched.
- Every scenario that writes, cleans up after itself, and **verifies** the cleanup.
- The athlete's calendar already contains a real third-party entry (`cycling-coach:…`, from enduragent).
  It is checked as untouched after every scenario — SC-004's "calendar seeded with foreign entries"
  needs no seeding.

---

## Scenario 0 — Record the calendar's starting state

Do this first, and after every scenario that writes.

```bash
uv run python -m scripts.calendar_state --describe
```

**Expect**: a listing of every event in the horizon, grouped by `external_id` prefix — `banister:` (ours),
`cycling-coach:` (enduragent's), and anything else. The foreign entry's id and content are the invariant
every later scenario asserts against.

## Scenario 1 — The session is on the watch in the morning (US1, SC-001, SC-009)

```bash
uv run pytest tests/test_providers/test_workout_dsl.py -v
```

Then, in Telegram: `/publish`, approve, and inspect the calendar.

**Expect**:

- Sessions appear on their planned dates, `category: WORKOUT` (FR-007).
- Each carries its steps as an executable structure — verify `workout_doc` is populated server-side and
  its `duration` matches the plan's `duration_minutes` (research R4 says these agree; assert it rather
  than trust it).
- **The device-forwarding caveat appeared before the first publication** (FR-012, SC-009). Confirm the
  athlete could actually complete that step from what they were told — this is the difference between the
  feature working and appearing to do nothing.
- A session with no steps (a legacy plan's session — spec 004 kept these loadable) is **refused and
  reported**, not published empty (FR-009).

## Scenario 2 — Nothing appears that the athlete did not agree to (US2, SC-002)

The requirement with the least tolerance for "probably fine".

```bash
uv run pytest tests/test_services/test_publication.py -k approval -v
```

**Expect**, exercised across *every* path capable of producing a write:

- No stored approval → nothing written (FR-001).
- Declining → nothing written, and no unprompted re-ask (FR-003).
- An approval whose `content_hash` no longer matches the plan → **refused**, fresh approval requested
  (FR-004). Test this by approving, then modifying the plan through the coach *before* publishing.
- Every `PublishedEntry` carries the `approval_id` that authorized it (FR-005).
- The post-publication report states what was *actually* written including failures, not a blanket
  success (FR-006).

**The check that matters most**: enumerate every call site that can reach `create_event`/`update_event`/
`delete_event` and confirm each is behind the approval gate. A grep, not a vibe — SC-002 says "verified
across every path capable of producing a write."

## Scenario 3 — Publishing twice does not duplicate (US3, SC-003)

**This scenario would fail against a naive implementation** — the API does not upsert (research R2).

```bash
# Publish the same period five consecutive times
for i in 1 2 3 4 5; do uv run python -m scripts.publish_horizon --approve-for-test; done
uv run python -m scripts.calendar_state --describe
```

**Expect**: exactly one `banister:` entry per planned session after five publications (SC-003) — not
five, not two. Then:

- The foreign `cycling-coach:` entry is present and unmodified (SC-004, FR-015).
- Each entry is traceable to its planned session via `external_id` (FR-013).
- **Interrupt a publication partway** (kill it mid-run) and retry: the calendar ends in the same state as
  an uninterrupted run (FR-016, SC-007), because `PublishedEntry` rows survived the restart.

## Scenario 4 — The calendar follows the plan (US4, SC-005)

```bash
# publish, then change the plan through the coach, then re-approve and republish
```

**Expect**:

- After re-approval, every future entry matches the revised plan (SC-005).
- A session removed from the plan has its entry **withdrawn**, not left behind (FR-019).
- Between the plan change and re-approval, the coach **says the calendar is out of date** (FR-020) rather
  than letting it look current.
- **Past-dated sessions are not rewritten** (FR-022) — check by changing a plan week that has already
  elapsed and confirming nothing in the past moved.

## Scenario 5 — The athlete's own edits are respected (US5, SC-008)

```bash
# publish, then edit one entry by hand at intervals.icu, then republish
```

**Expect**:

- The edited entry is **not silently overwritten** — the athlete is told and decides (FR-023).
- An entry the athlete *deleted* is not silently recreated (FR-024).
- A completed activity is never altered (FR-025) — verify against a date holding a real completed ride.

This is the scenario most likely to be skipped and most damaging to skip: silently overwriting a
deliberate edit teaches the athlete not to touch their own calendar.

## Scenario 6 — Withdrawal (FR-021, SC-006)

```bash
uv run python -m scripts.calendar_state --withdraw-all --confirm
uv run python -m scripts.calendar_state --describe
```

**Expect**: 100% of `banister:` entries gone, 0% of anything else (SC-006). The foreign entry survives.
This is the scenario that proves the prefix scoping is real rather than intended.

## Scenario 7 — Failures are coherent and reported (FR-026, FR-027, FR-028)

**Expect**:

- A session the calendar rejects is reported **specifically**, and the rest of the publication continues
  (FR-028) — do not let one bad session abandon six good ones.
- A mid-publication connection failure leaves the calendar coherent and the athlete informed (FR-026),
  with the already-written entries recorded so a retry resumes rather than duplicates.
- Quota headroom is confirmed by arithmetic, not by exhausting it: a full horizon is ~10 requests against
  ~5000/day (SC-010). Deliberately not stress-tested — the same call spec 002 made about rate limits.

---

## Definition of done

- [ ] Scenario 0 run before and after every writing scenario, with the foreign entry verified untouched
- [ ] All scenarios pass
- [ ] Five consecutive publications produce exactly one entry per session (SC-003), demonstrated — not argued
- [ ] Every write path enumerated and confirmed behind the approval gate (SC-002)
- [ ] An interrupted publication, retried, reaches the same state as an uninterrupted one (SC-007)
- [ ] An athlete-edited entry is demonstrably not overwritten (SC-008)
- [ ] Withdrawal removes 100% of ours and 0% of anything else (SC-006)
- [ ] The device-forwarding caveat verified as actionable by the athlete, not just present (SC-009)
- [ ] Full suite green; no new lint violations beyond the current baseline
- [ ] Every test-written calendar entry cleaned up, verified by a final Scenario 0 run
