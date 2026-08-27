# Banister

Self-hosted AI training coach in Telegram. Generates personalized training plans based on the Banister impulse-response model (ATL/CTL/TSB), tracks your progress, and adapts through natural conversation.

**Single-user. Runs locally. No cloud dependency.**

---

## What it does

- Generates a structured training plan (Base / Build / Peak / Taper) calibrated to your available hours, FTP or heart rate, and target event
- Automatically logs activities from Strava via webhook — computes TSS, zones, quality metrics
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
| Sport integration | Strava API v3 |
| Runtime | Python 3.13 + uv |

---

## Quick start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- A Telegram bot token — create one with [@BotFather](https://t.me/BotFather)
- Your Telegram user ID — get it with [@userinfobot](https://t.me/userinfobot)
- An Anthropic or OpenRouter API key

Strava is optional. The bot works without it — you log sessions manually.

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

## Strava integration (optional)

### Setup

1. Create a Strava app at [strava.com/settings/api](https://www.strava.com/settings/api)
2. Set the **Authorization Callback Domain** to your public domain
3. Add to `.env`:

```env
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REDIRECT_URI=https://your.domain.com/auth/strava/callback
STRAVA_STATE_SECRET=a_random_32_char_secret
STRAVA_WEBHOOK_VERIFY_TOKEN=another_random_secret
```

4. In production, expose port 8000 via a reverse proxy (see `nginx/`) and set:

```env
ENVIRONMENT=production
TELEGRAM_WEBHOOK_URL=https://your.domain.com/webhook/telegram
```

### Local development with Strava

Use [ngrok](https://ngrok.com) to expose your local port:

```bash
ngrok http 8000
# Set the ngrok URL as STRAVA_REDIRECT_URI in .env and in your Strava app settings
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
| `/strava` | Connect or disconnect Strava |
| `/reminders` | Manage morning session reminders |
| `/cancel` | Cancel current action |
| `/help` | Command list |

---

## Project structure

```
app/
├── main.py              # FastAPI entry point + Strava OAuth callback
├── config.py            # Settings (pydantic-settings, loaded from .env)
├── bot/
│   ├── routers/
│   │   ├── setup.py     # /setup FSM — profile + plan generation
│   │   ├── plan.py      # /plan, /week N
│   │   ├── forme.py     # /forme — ATL/CTL/TSB display
│   │   ├── recap.py     # /recap — weekly adherence
│   │   ├── strava.py    # Strava connect/disconnect
│   │   ├── session_log.py  # Manual session logging + RPE
│   │   ├── reminders.py # Reminder settings
│   │   ├── chat.py      # Free-form coaching chat (catch-all)
│   │   └── common.py    # /start, /help, /cancel
│   ├── middlewares/
│   │   ├── db_session.py   # Injects AsyncSession into every handler
│   │   └── single_user.py  # Owner guard + user injection (no upsert)
│   ├── keyboards/       # Inline keyboard builders
│   ├── states.py        # FSM states: SetupStates, PlanStates, SessionLogStates
│   └── setup.py         # Dispatcher + middleware + router registration
├── db/
│   ├── client.py        # AsyncEngine (local SQLite)
│   ├── models/          # SQLAlchemy ORM models
│   └── repositories/    # Data access layer — no SQL in handlers
├── engine/              # Deterministic engine — zero LLM
│   ├── plan_builder.py  # Main plan generator
│   ├── periodization.py # Phase sequencing (Base/Build/Peak/Taper)
│   ├── zones.py         # Power and HR zone computation
│   ├── tss.py           # TSS / HRSS calculation
│   ├── atl_ctl.py       # ATL/CTL/TSB (Banister impulse-response model)
│   ├── adherence_kpi.py # Session KPI scoring (0–2.0 pts)
│   └── schemas.py       # Pydantic: AthleteProfileSchema, TrainingPlanSchema
├── llm/
│   ├── providers/       # Anthropic + OpenRouter (common interface)
│   ├── chat.py          # Conversation orchestration
│   ├── activity_analysis.py  # Post-session narrative
│   ├── narrator.py      # Plan narration
│   └── prompts.py       # System prompts
└── strava/
    ├── oauth.py         # OAuth flow + HMAC state signing
    ├── webhook.py       # Activity event handler (TSS, zones, KPI, notification)
    ├── analyzer.py      # RawActivity → AnalyzedSession
    ├── matching.py      # Activity ↔ planned session semantic scoring
    └── history.py       # Historical activity import
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
