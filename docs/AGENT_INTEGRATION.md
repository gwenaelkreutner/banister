# Plugging an agent into Banister

Banister ships with its own Telegram coach, and that remains the default: nothing here is needed to
use it. If you already run an AI agent, the agent API lets it coach with Banister instead, with
your agent's model, memory and channels.

**The split:** your agent understands the athlete and chooses or composes sessions. Banister computes
every number and reports risks. Only the athlete's own gesture writes to their calendar.

## Enabling it

Set a token in `.env` and restart. Without one the API answers `404`.

```bash
AGENT_API_TOKEN=$(openssl rand -hex 32)
```

The port is bound to `127.0.0.1`. Do not route `/agent/v1` publicly. Banister is single-athlete, so
every call concerns that athlete. Send `Authorization: Bearer <AGENT_API_TOKEN>` on each request.

## Hermes Agent

A ready-made plugin lives in `integrations/hermes/`, with tools, approval buttons, a coaching skill
and a check of the agent's final reply:

```bash
BANISTER_AGENT_TOKEN=... OWNER_CHAT_ID=<your Telegram user id> ./integrations/hermes/install.sh
# add TELEGRAM_BOT_TOKEN to ~/.hermes/profiles/banister/.env, then:
hermes gateway restart
```

Give the profile its **own** Telegram bot: one bot token cannot be polled by two programs, and
Banister keeps sending ride notifications, reminders and recaps through its own bot.

## Writing your own integration

Any agent works (an MCP server, for instance): it is plain HTTP. Three rules keep the guarantees:

1. **Do not expose `/approve` or `DELETE /publications/{id}` as tools.** Call them from code, in
   response to the athlete's tap or confirmation. Otherwise a model can write to their calendar.
2. **Show `card_text` as is** when asking for approval. It only states computed values.
3. **Pass the final reply through `/verify-reply`**, with the results your agent received.

## Endpoints

All under `/agent/v1`.

| Method and path | Purpose |
|---|---|
| `GET /context?days=&ahead=` | Dated form, recovery, guardrail findings, recent rides, planned sessions, profile |
| `GET /fitness?days=&granularity=` | CTL/ATL/TSB history |
| `GET /trend?days=` | Weekly totals |
| `GET /wellness?metrics=&days=&granularity=` | HRV, resting heart rate, sleep... |
| `GET /rides/{date}` | The rides of a past day |
| `GET /memory?keyword=&category=&days=` | Durable notes and dated journal |
| `POST /memory/notes` | `{"category", "note"}` adds a note |
| `GET /sessions/catalog?workout_type=&family=&phase=&q=&max_minutes=&detail=` | Search the library, several results |
| `GET /sessions/recommendation?workout_type=&max_minutes=&duration_minutes=` | The engine's pick from today's state |
| `POST /sessions/evaluate` | Compute a session, writes nothing |
| `POST /proposals` | Record a session to approve, writes nothing to the calendar |
| `GET /proposals/{id}` | Its status |
| `POST /proposals/{id}/approve` | **Athlete only.** Publishes, at most once. A failed write can be retried |
| `POST /proposals/{id}/decline` | Closes it |
| `GET /publications` | Published sessions not withdrawn |
| `GET /publications/{id}/withdrawal-card` | Text and buttons to confirm a withdrawal |
| `DELETE /publications/{id}` | **Athlete only.** Removes it from the calendar |
| `POST /verify-reply` | `{"text", "retrieved"}` returns the text to send |

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

`kind` is one of `context`, `fitness`, `trend`, `wellness`, `ride`, `session` (evaluate or proposal
answers) or `recommendation`, and `result` is the answer as received. Check before anything reaches
the athlete: with a streaming display, the text would already be out.

## Not available through this API yet

Ride notifications, RPE, reminders, the weekly recap, setup and training plans remain in Banister's
own bot.
