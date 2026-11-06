# Plugging an agent into Banister

Banister ships with its own Telegram coach, and that remains the default: nothing here is needed to
use it. If you already run an AI agent, it can become the coach instead, with its own model, memory
and channels.

**The split:** your agent understands the athlete, chooses or composes sessions and writes the
coaching. Banister computes every number, reports risks and keeps the data. Only the athlete's own
gesture changes their calendar or their plan.

## Two interfaces, one core

| | `COACH_INTERFACE=telegram` (default) | `COACH_INTERFACE=agent` |
|---|---|---|
| Who talks to the athlete | Banister's bot | Your agent |
| Ride notifications, reminders, weekly figures | Sent by the bot | Same messages, delivered by your agent from the outbox |
| Coaching text | Banister's LLM settings | Your agent. Banister makes no LLM call for it |
| Banister's bot | Polling | Silent |

The agent API works in both modes, for an agent that only reads or proposes next to the bot. Set the
athlete up first with `/setup` on the bot, then switch: `/setup` has no agent equivalent yet.

## Enabling it

Set these in `.env` and restart. Without a token the API answers `404`.

```bash
AGENT_API_TOKEN=$(openssl rand -hex 32)
COACH_INTERFACE=agent   # optional: let the agent take over from the bot
```

The port is bound to `127.0.0.1`. Do not route `/agent/v1` publicly. Banister is single-athlete, so
every call concerns that athlete. Send `Authorization: Bearer <AGENT_API_TOKEN>` on each request.

To go back, set `COACH_INTERFACE=telegram` and restart: nothing else changes, all data is shared.

## Hermes Agent

A ready-made plugin lives in `integrations/hermes/`: tools, delivery of messages, buttons, a coaching
skill and a check of the agent's final reply.

```bash
BANISTER_AGENT_TOKEN=... OWNER_CHAT_ID=<your Telegram user id> ./integrations/hermes/install.sh
# add TELEGRAM_BOT_TOKEN to ~/.hermes/profiles/banister/.env, then:
hermes gateway restart
```

Use a Telegram bot other than Banister's, so going back to `telegram` stays a one-line change.

Send the bot a first message after installing: Hermes only lets a plugin continue a chat that a
person has started, and until then the coach cannot follow up on its own.

## Writing your own integration

Any agent works (an MCP server, for instance): it is plain HTTP. Your integration has two sides.

**What the model may call** is everything below except `/actions`, `/messages` and `/verify-reply`.

**What only your code does:**

1. **Deliver messages.** Poll `GET /messages/pending`, send each one in order, then
   `POST /messages/{id}/delivered`. Each has `text`, `parse_mode`, `silent` and `buttons`.
2. **Show cards.** A proposal returns a `card` (`text`, `parse_mode`, `buttons`). Show it as is: it
   only states computed values.
3. **Carry taps.** A button has a `label` and an opaque `action`. Send it to `POST /actions`. Replace
   the card with the returned `text`. On an error, show `detail` and keep the card.
   **Never expose `/actions` to a model**, or it can write to the athlete's calendar.
4. **Hand over to the agent.** When a message or an action result has an `agent_prompt`, start a turn
   of your agent with it. That is how a ride review or the weekly analysis gets written. If your
   agent cannot start a turn right now, show `handover_hint` instead of losing it silently.
5. **Check the final reply** with `/verify-reply`, before anything reaches the athlete.

Actions fit Telegram's 64-byte limit with a prefix of up to 4 bytes.

## Endpoints

All under `/agent/v1`.

| Method and path | Purpose |
|---|---|
| `GET /context?days=&ahead=` | Dated form, recovery, guardrail findings, recent rides, planned sessions, profile |
| `GET /fitness?days=&granularity=` | CTL/ATL/TSB history |
| `GET /trend?days=` | Weekly totals |
| `GET /wellness?metrics=&days=&granularity=` | HRV, resting heart rate, sleep... |
| `GET /rides/{date}` | The rides of a past day, with `reviews`: everything measured |
| `GET /recap` | The week's figures |
| `GET /power-profile` | Power curve changes and what can be sustained |
| `GET /memory?keyword=&category=&days=` | Durable notes and dated journal |
| `POST /memory/notes` | `{"category", "note"}` adds a note |
| `PUT /injury` | `{"location", "severity", "estimated_recovery_days"}` |
| `GET`, `PUT /reminders` | `{"enabled", "hour", "minute"}` |
| `GET /sessions/catalog?workout_type=&family=&phase=&q=&max_minutes=&detail=` | Search the library, several results |
| `GET /sessions/recommendation?workout_type=&max_minutes=&duration_minutes=` | The engine's pick from today's state |
| `POST /sessions/evaluate` | Compute a session, writes nothing |
| `POST /proposals` | A session to approve: returns a `card` |
| `GET /proposals/{id}` | Its status |
| `GET /publications` | Published sessions not withdrawn |
| `GET /publications/{id}/withdrawal-card` | A `card` to confirm removing one |
| `GET /plan?week=` | The active plan, one week in detail. `mode: freestyle` means none |
| `POST /plan/proposals/new` | `{"goal", "target_date"}` computes a plan: returns a `card` |
| `POST /plan/proposals/leave` | A `card` to go back to freestyle |
| `POST /plan/proposals/week` | `{"reason", "modification_type", "week_offset"}`: returns a `card` |
| `POST /plan/proposals/session` | `{"constraint_type", "day_offset", "action"}`: returns a `card` |
| `POST /plan/calendar/publish-request` | A `card` to publish the plan's coming sessions |
| `POST /plan/calendar/withdraw-request` | A `card` to remove them |
| `GET /messages/pending`, `POST /messages/{id}/delivered` | **Code only.** The outbox |
| `POST /actions` | **Code only, on the athlete's tap.** `{"action"}` |
| `POST /verify-reply` | **Code only.** `{"text", "retrieved"}` returns the text to send |

## Describing a session

`POST /sessions/evaluate` and `POST /proposals` take a library template, optionally fitted to a
duration, or a structure you compose:

```json
{"date": "2026-11-07", "template_id": "threshold-3x12", "duration_minutes": 75}
```

```json
{
  "date": "2026-11-07",
  "workout_type": "endurance",
  "title": "Group ride, easy on the climbs",
  "steps": [{"kind": "steady", "duration_minutes": 180, "zone_code": "Z2"}]
}
```

- `workout_type`: `recovery`, `endurance`, `intervals`, `long_ride`.
- A step has a `kind` (`warmup`, `work`, `recovery`, `cooldown`, `steady`), a `duration_minutes` and a
  `zone_code` (`Z1` to `Z6`). Group steps with `{"repeat": 3, "steps": [...]}`.
- Intensity is always a zone. Watts and heart rate are resolved from the athlete's own reference.

The answer gives `duration_minutes`, `tss_estimate`, `zone_targets`, `engine_reference`, `fitness`,
`guardrails`, `warnings`, `scheduled_that_day` and `publishable`.

`engine_reference` is a modest target for one ordinary day, derived from chronic load, with
`recent_peak_day`, the biggest day of the last six weeks. `above_reference_load` only fires for a
session beyond both.

**Findings report, they do not block.** A session above the recommended load is evaluated and can be
proposed, with warnings: the athlete decides. `422` is only for a session that cannot exist, such as
a bad structure or a date outside today to +28 days.

Findings describe the athlete's state on `evaluated_as_of`, not a forecast of `date`: load on a future
day depends on rides that have not happened. `tss_estimate` comes from zones and durations, it is not
a measurement. Without FTP or heart rate reference, `publishable` is `false` rather than guessed.

## `/verify-reply`

Same check as the built-in coach. A sentence stating a metric that disagrees with the data, or that
was never retrieved, is replaced by a notice and the failure recorded. Current CTL, ATL, TSB, FTP and
guardrail metrics are read by Banister. Pass what else your agent received:

```json
{"text": "...", "retrieved": [{"kind": "session", "result": {"tss_estimate": 140.0}}]}
```

`kind` is one of `context`, `fitness`, `trend`, `wellness`, `ride`, `recap`, `plan`, `plan_proposal`,
`session` (evaluate or proposal answers) or `recommendation`, and `result` is the answer as received. Check before anything reaches
the athlete: with a streaming display, the text would already be out.

## Not available through this API yet

First-time `/setup`, and `/reset`: use the bot for those, with `COACH_INTERFACE=telegram`.
