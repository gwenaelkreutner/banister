# Personas

A persona defines your coach's identity, language, and conversational style.
Banister ships four examples. You can write your own and select it with `PERSONA=your-file` in `.env`.

## Quick start

1. Copy an existing file: `cp coach-default.yaml my-coach.yaml`
2. Edit the `name`, `language`, and prompt fields
3. Set `PERSONA=my-coach` in your `.env`
4. Restart Banister — the new persona is active immediately

## YAML schema

```yaml
name: string          # Display name (used in logs, not shown to athlete)
language: en | fr     # Controls which strings.py language class is used in the bot UI
voice: string         # Tag for documentation purposes (not machine-interpreted yet)

system_prompt: |      # REQUIRED — injected as the LLM system prompt for the coaching chat
  ...

ux_prompt: |          # REQUIRED — injected for UX/conversational responses (shorter exchanges)
  ...
```

### Template variables

These placeholders are filled at runtime before the prompt reaches the LLM:

| Variable | Value |
|----------|-------|
| `{first_name}` | Athlete's Telegram first name |
| `{tsb_label}` | Human-readable TSB state (e.g. "fresh", "moderate fatigue") |
| `{event_name}` | Name of the nearest target event, or empty string |

### Rules the LLM must follow (enforce in your prompts)

All personas should include these constraints to maintain data integrity:

- **Never recalculate** — interpret the pre-calculated values provided, never recompute them
- **Never invent numbers** — use the available tools (`propose_session_adjustment`, etc.) to retrieve data
- **Respect tool mapping** — 1-day constraint → `propose_session_adjustment`; multi-day → `propose_plan_modification`

## Shipped personas

| File | Language | Style |
|------|----------|-------|
| `coach-default.yaml` | English | Direct expert, the default for new installs |
| `pace.yaml` | French | The original Banister persona, direct and technical |
| `analyst.yaml` | English | Metrics-first, minimal narrative, no emotional framing |
| `zen.yaml` | English | Recovery-focused, gentle, sustainability over peak output |

## Good first contribution

Add a persona in your language or with a different coaching style and open a PR.
See [CONTRIBUTING.md](../CONTRIBUTING.md) for the full guide.
