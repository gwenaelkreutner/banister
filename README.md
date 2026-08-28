# Banister

Self-hosted AI training coach in Telegram. Generates personalized training plans based on the Banister impulse-response model (ATL/CTL/TSB), tracks your progress, and adapts through natural conversation.

**Single-user. Runs locally. No cloud dependency.**

---

## What it does

- Generates a structured training plan (Base / Build / Peak / Taper) calibrated to your available hours, FTP or heart rate, and target event
- Automatically detects new activities from intervals.icu — training load, zones, and quality metrics are consumed from the source, never recomputed
- Tracks your fitness curve (ATL/CTL/TSB) after every session
- Sends morning reminders with the day's session
- Weekly adherence recap with KPI score
- Free-form coaching chat with your context (plan, recent load, fitness metrics)

**Core principle: the LLM never computes load. All calculations (TSS, zones, ATL/CTL/TSB) are deterministic. The LLM handles narration, coaching tone, and conversational adaptation.**

---

## Stack

| Layer | Technology |
|---|---|
| Bot | aiogram v3 (async FSM) |
| API | FastAPI (webhooks + OAuth) |
| Database | SQLite (local file, no separate service) |
| ORM | SQLAlchemy async + aiosqlite |
| LLM | Anthropic Claude / OpenRouter |
| Sport integration | intervals.icu (personal API key, periodic polling) |
| Runtime | Python 3.13 + uv |

---

## Quick start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A Telegram bot token — create one with [@BotFather](https://t.me/BotFather)
- Your Telegram user ID — get it with [@userinfobot](https://t.me/userinfobot)
- An Anthropic or OpenRouter API key
- An [intervals.icu](https://intervals.icu) account and API key — Settings → Developer Settings → API Key.
  intervals.icu is the only supported activity source; there is no manual logging fallback.

### 1. Clone

```bash
git clone <repo>
cd banister
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your values. Minimum required:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_OWNER_ID=your_telegram_user_id
ANTHROPIC_API_KEY=sk-ant-...
INTERVALS_API_KEY=your_intervals_api_key
```

No database configuration needed — data lives in a local SQLite file, created automatically.

### 3. Start

```bash
make up
```

This builds the Docker image and starts the bot in polling mode. The database schema is created and
migrated automatically on first start — no separate database service, no manual step.

### 4. Configure your profile

Open Telegram, find your bot, and run `/setup`. Answer 7 questions — your plan is generated immediately.

---

## intervals.icu integration

No OAuth, no callback URL, no app registration — just a personal API key.

1. Go to intervals.icu → Settings → Developer Settings → API Key
2. Add it to `.env`:

```env
INTERVALS_API_KEY=your_intervals_api_key
# INTERVALS_ATHLETE_ID=0                 # optional — "0" resolves to the key's own athlete
INTERVALS_POLL_INTERVAL_MINUTES=5        # optional — how often new activities are checked for
```

The key's validity is checked at startup; an invalid or revoked key makes the app refuse to start rather
than run in a half-working state.

New activities are detected by periodic polling, not a webhook — there is no inbound endpoint to expose
and no reverse proxy or tunnel needed for this integration. In production, only the Telegram webhook needs
a public URL:

```env
ENVIRONMENT=production
TELEGRAM_WEBHOOK_URL=https://your.domain.com/webhook/telegram
```

---

## Bot commands

| Command | Description |
|---|---|
| `/setup` | Configure your profile and generate a plan (re-run to regenerate) |
| `/plan` | View the current week's sessions |
| `/week N` | View week N of your plan |
| `/forme` | Current fitness metrics (ATL / CTL / TSB) |
| `/recap` | Weekly adherence recap and KPI score |
| `/reminders` | Manage morning session reminders |
| `/cancel` | Cancel current action |
| `/help` | Command list |

---

## Project structure

```
app/
├── main.py              # FastAPI entry point + Telegram webhook
├── config.py            # Settings (pydantic-settings, loaded from .env)
├── bot/
│   ├── routers/
│   │   ├── setup.py     # /setup FSM — profile + plan generation
│   │   ├── plan.py      # /plan, /week N
│   │   ├── forme.py     # /forme — ATL/CTL/TSB display
│   │   ├── recap.py     # /recap — weekly adherence
│   │   ├── session_log.py  # Perceived-exertion (RPE) capture after a detected activity
│   │   ├── reminders.py # Reminder settings
│   │   ├── chat.py      # Free-form coaching chat (catch-all)
│   │   └── common.py    # /start, /help, /cancel
│   ├── middlewares/
│   │   ├── db_session.py   # Injects AsyncSession into every handler
│   │   └── single_user.py  # Owner guard + user injection (no upsert)
│   ├── keyboards/       # Inline keyboard builders
│   ├── states.py        # FSM states: SetupStates, PlanStates
│   └── setup.py         # Dispatcher + middleware + router registration
├── db/
│   ├── client.py        # AsyncEngine (local SQLite)
│   ├── models/          # SQLAlchemy ORM models
│   └── repositories/    # Data access layer — no SQL in handlers
├── engine/              # Deterministic engine — zero LLM
│   ├── plan_builder.py  # Main plan generator — selects from session_library.py
│   ├── periodization.py # Phase sequencing (Base/Build/Peak/Taper)
│   ├── session_library.py  # Loads/validates sessions/*.yaml, deterministic selection
│   ├── fitting.py       # Adapts a template to a load target (not yet wired into generation)
│   ├── session_render.py   # Session description, derived from structure, language-aware
│   ├── atl_ctl.py       # ATL/CTL/TSB (Banister impulse-response model)
│   ├── adherence_kpi.py # Session KPI scoring (0–2.0 pts)
│   └── schemas.py       # Pydantic: AthleteProfileSchema, TrainingPlanSchema, Step, RepeatGroup
├── llm/
│   ├── providers/       # Anthropic + OpenRouter (common interface)
│   ├── chat.py          # Conversation orchestration
│   ├── activity_analysis.py  # Post-session narrative
│   ├── narrator.py      # Plan narration
│   └── prompts.py       # System prompts
├── services/
│   ├── weekly_recap.py       # /recap orchestration
│   └── activity_feedback.py  # Post-activity context assembly, no bot dependency
└── providers/
    ├── intervals/       # intervals.icu client, mapper, poller, notifier, wellness
    └── analysis/        # Activity ↔ plan matching, highlight/personal-record selection
sessions/                # Session template library (YAML, contributor-editable, no code change needed)
migrations/
├── env.py               # Alembic environment
└── versions/            # Schema revisions — applied automatically at startup
tests/                   # Engine unit tests (zones, TSS, periodization, plan, matching)
eval/                    # Offline plan quality evaluation framework
```

---

## Make commands

```bash
make up          # Build and start (Docker)
make down        # Stop containers
make logs        # Follow app logs
make backup      # Snapshot the SQLite database into data/backup_YYYYMMDD_HHMMSS.db
make reset-db    # Wipe and reinitialize the database (deletes the data volume)
make shell       # Open a shell in the app container
```

---

## Development (without Docker)

```bash
# Install dependencies
uv sync

# No database setup needed — a SQLite file is created and migrated automatically on first
# start, under ./data by default. Set DATA_DIR in .env to use a different location.

# Run
python -m uvicorn app.main:app --port 8000 --reload
```

---

## Tests

```bash
uv run pytest tests/ -v
uv run ruff check app/ tests/
```

---

## Security note

`TELEGRAM_OWNER_ID` is enforced at the middleware level — all messages from other Telegram users are silently dropped. Set it to your own Telegram ID before starting the bot.
