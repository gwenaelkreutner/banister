# Banister

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](Dockerfile)
[![CI](https://github.com/gwenaelkreutner/banister/actions/workflows/ci.yml/badge.svg)](https://github.com/gwenaelkreutner/banister/actions/workflows/ci.yml)

**Self-hosted AI cycling coach in Telegram.** Generates a personalized training plan, watches
your real rides on [intervals.icu](https://intervals.icu), adapts through conversation, and
flags overtraining before it becomes an injury — with a deterministic engine underneath so the
LLM never gets to invent your training load.

**Single user. One SQLite file. No cloud dependency, no subscription, no data leaving your
server except calls to your LLM provider.**

---

## Why

Most AI "coach" bots are a thin chat wrapper: you ask a question, an LLM guesses an answer from
whatever you typed, and every number it gives you is a hallucination risk. Banister splits the
two jobs on purpose:

- **A deterministic engine computes everything that matters** — training load (TSS), zones,
  periodization, fitness curve (ATL/CTL/TSB), overtraining risk (ACWR, ramp rate, monotony). Pure
  Python, zero LLM, fully unit-tested. The LLM never touches a training-load calculation.
- **The LLM only narrates and converses** — it explains what the engine computed, answers
  questions with your real data as context, and every number it states in a reply gets checked
  against what was actually retrieved before the message is sent.
- **Your training data is never recomputed behind your back.** Load, zones, and thresholds come
  straight from intervals.icu, the source you already trust — Banister only adds what the source
  doesn't provide (periodization, matching, guardrails).
- **It runs on your own hardware.** One container, one SQLite file, no managed database, no
  vendor lock-in. Point it at Anthropic or OpenRouter and it's yours.

---

## What it does

- Generates a structured plan (Base / Build / Peak / Taper) from your FTP or heart rate,
  available hours, and target event — or skip the plan entirely and train in **freestyle mode**,
  where the coach suggests one session at a time based on your current form
- Detects new activities from intervals.icu automatically and tracks your fitness curve
  (ATL/CTL/TSB) after every session
- Raises training guardrails — acute:chronic load ratio, ramp rate, monotony, HRV/resting-HR
  trends — before they become a wall, and checks its own stated numbers against what was actually
  retrieved before replying
- Pushes planned sessions to your intervals.icu calendar on explicit approval (`/publish`) — your
  watch picks them up from there
- Weekly adherence recap with a KPI score, and on-demand relecture of any logged session
  (`/review`)
- Free-form coaching chat with real context: your plan, recent load, fitness metrics, and a
  long-term memory of patterns it's noticed about you
- Natural-language meal logging in the same chat, with a deterministic daily calorie total
- Multiple coach voices (`/voice`) — direct, analytical, or calm — swappable per athlete, no code
  change required

---

## What this is not

Banister is coaching software, **not a physician and not a certified coach**. The sessions it
proposes are suggestions — you always decide. Its training guardrails raise flags and offer
adjustments; they do not diagnose illness or injury and must not be relied on as medical opinion.
If something concerns you about your health, see a qualified professional. This disclaimer is
also shown in the app at the end of first-time setup.

---

## Stack

| Layer | Technology |
|---|---|
| Bot | aiogram v3 (async FSM) |
| API | FastAPI (Telegram webhook only) |
| Database | SQLite — one local file, no separate service |
| ORM | SQLAlchemy async + aiosqlite, Alembic migrations (automatic) |
| LLM | Anthropic Claude or OpenRouter (swappable) |
| Sport data | intervals.icu (personal API key, periodic polling — no OAuth, no webhook to expose) |
| Runtime | Python 3.13 + uv |

---

## Quick start

### Prerequisites

- [Docker](https://www.docker.com/products/docker-desktop/)
- A Telegram bot token — create one with [@BotFather](https://t.me/BotFather)
- Your Telegram user ID — get it with [@userinfobot](https://t.me/userinfobot)
- An Anthropic or OpenRouter API key
- An [intervals.icu](https://intervals.icu) account and API key (Settings → Developer Settings →
  API Key) — the only supported activity source, no manual logging fallback

### Run it

```bash
git clone https://github.com/gwenaelkreutner/banister.git
cd banister
cp .env.example .env
```

Edit `.env` — minimum required:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_OWNER_ID=your_telegram_user_id
ANTHROPIC_API_KEY=sk-ant-...
INTERVALS_API_KEY=your_intervals_api_key
```

Banister uses English by default. Set `APP_LANGUAGE=fr` in `.env` before starting or
upgrading an installation if you want to keep the French bot experience. The setting
applies to the whole single-user installation after a restart; it does not change your
coach voice or rewrite existing training history.

```bash
make up
```

Schema creation and migrations run automatically on first start — no separate database step.
Open Telegram, find your bot, run `/setup`, answer a few questions your intervals.icu account
can't already answer for you — your plan is generated immediately.

---

## Bot commands

| Command | Description |
|---|---|
| `/setup` | Confirm your profile (read from intervals.icu) and generate a plan |
| `/goal` | Change objective, or switch between plan mode and freestyle mode |
| `/plan` / `/week N` | View the current or a specific week |
| `/fitness` (`/forme` also works) | Current fitness metrics (ATL / CTL / TSB) + power-curve trends |
| `/summary` (`/recap` also works) | Weekly adherence recap and KPI score |
| `/review` | Review a recently logged session |
| `/publish` / `/unpublish` | Push planned sessions to your intervals.icu calendar, or withdraw them |
| `/reminders` | Manage morning session reminders |
| `/voice` | Switch coach persona |
| `/reset` | Wipe your data and start over |
| `/help` | Command list |

Anything else you type goes to the free-form coaching chat.

---

## Contributing without touching Python

Two things are plain YAML, loaded at startup, no code change required:

- **`sessions/*.yaml`** — the workout template library the plan generator and freestyle mode draw
  from. See [`sessions/README.md`](sessions/README.md).
- **`personas/*.yaml`** — coach voices (tone, vocabulary, opening rules). See
  [`personas/README.md`](personas/README.md).

For anything touching `app/`, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system
shape, and `CLAUDE.md` for the file-by-file map. Run `uv run pytest tests/ -v` and
`uv run ruff check app/ tests/` before opening a PR — CI runs the same.

---

## Development (without Docker)

```bash
uv sync
python -m uvicorn app.main:app --port 8000 --reload
```

A SQLite file is created and migrated automatically on first start, under `./data` by default
(`DATA_DIR` in `.env` to change it).

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

## Security note

`TELEGRAM_OWNER_ID` is enforced at the middleware level — messages from any other Telegram user
are silently dropped. Set it to your own Telegram ID before starting the bot.

---

## License

[MIT](LICENSE) — do what you want with it, including running it for someone else, as long as the
license notice stays attached.
