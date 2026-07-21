# QueueCortex

A personal ticket-tracker and analytics dashboard for L2 support agents working Trinity tickets. It syncs your tickets from Trinity (Emergent's internal support tool, via MCP) into a local SQLite database, then gives you a fast dashboard, full open/close/reopen history per ticket, and day/month/year performance analytics.

## What it does

- **Dashboard** — search/filter your tickets, add a ticket by number, see status, type (derived from tags), assignment, and an expandable per-ticket open/close/reopen timeline.
- **Accurate "closed today"** — a ticket only counts as closed today if it is *currently* sitting closed and the reporting day of its last close is today. Reopens (including customer-triggered reopens) are tracked and broken out separately, so the daily count can't be inflated by same-day reopen/reclose churn.
- **Assignment tracking** — flags when a ticket is taken from you or unassigned by another agent or by the system, not just when you release it yourself.
- **Analytics** — day/month/year performance views (opened, closed, reopened) built from an append-only event log, so historical numbers never drift.
- **Settings** — auto-save-on-blur config for sync/reporting behavior, plus a tag → ticket-type mapping table (Trinity tags are freeform, so this is what drives categorization).

## Architecture

| | |
|---|---|
| **Backend** | Python 3.11, FastAPI + SQLAlchemy (async) + SQLite (WAL mode), in [backend/](backend/). Talks to Trinity over MCP (streamable HTTP), backfills full ticket history on first run, then polls incrementally every N minutes (configurable) plus a manual "Sync now". |
| **Frontend** | React 19 + Vite + TypeScript + Tailwind CSS v4 + TanStack Query + Recharts, in [frontend/](frontend/). |
| **Auth** | Email + 4-digit OTP, single allowed login email, signed session cookie — no third-party auth provider. |

Data model, in brief: `tickets` is a cached current-state row per ticket; `ticket_events` is the append-only source of truth pulled from Trinity's audit trail; `status_transitions` has **one row per status-change event** (not per ticket), which is what makes "closed today" accounting correct without special-case code.

## Prerequisites

- **Python 3.11** (the backend depends on this specific minor version — check with `python --version` / `py -3.11 --version`)
- **Node.js 20+** and npm
- A **Trinity MCP token** for your own Emergent account (Trinity Settings → MCP Keys). This app is scoped to *your* Trinity identity — everyone running it needs their own token, not a shared one.
- A Gmail address + [App Password](https://myaccount.google.com/apppasswords) if you want real OTP emails sent (optional — see below)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Yashwanth2408/QueueCortex.git
cd QueueCortex
```

### 2. Backend

```bash
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy the environment template and fill it in:

```bash
copy .env.example .env
```

Open `backend/.env` and set at minimum:

| Variable | What to put |
|---|---|
| `TRINITY_MCP_TOKEN` | Your own Trinity MCP token (starts with `tmcp_live_`) |
| `TRACKED_AGENT_EMAIL` | Your Emergent email — the agent identity the app tracks stats for |
| `ALLOWED_LOGIN_EMAIL` | The only email allowed to log into the dashboard (usually the same as above) |
| `SESSION_SECRET` | Any random string — used to sign login sessions. Generate one with: `python -c "import secrets; print(secrets.token_hex(32))"` |

Everything else in `.env.example` has a sensible default (poll interval, timezone, ports). SMTP (`SMTP_*`) is optional — if left blank, the OTP code is shown directly on the login screen instead of being emailed, which is fine for local personal use.

Run database migrations, then start the API:

```bash
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

The backend is now running at `http://127.0.0.1:8000`.

### 3. Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. It proxies `/api` requests to the backend on port 8000 — no separate frontend config needed.

### 4. Log in and do the first sync

1. Enter your `ALLOWED_LOGIN_EMAIL` on the login screen and submit the OTP (shown on-screen if SMTP isn't configured).
2. Click **Sync now** on the Dashboard to pull your full ticket history from Trinity for the first time. This can take a minute depending on how many tickets you have.
3. After that, the backend polls Trinity automatically every `POLL_INTERVAL_MINUTES` (default 20) — "Sync now" is there for whenever you want it immediately.

## Project layout

```
backend/
  app/
    api/          FastAPI routes (auth, tickets, analytics, settings, sync, tags)
    sync/         Trinity → local DB sync engine
    models.py     SQLAlchemy models
    derive.py     Shared computation (status snapshots, flags)
    config.py     Settings (reads backend/.env)
  alembic/        DB migrations
  requirements.txt
frontend/
  src/
    pages/        Dashboard, Analytics, Settings, Login
    components/    UI primitives + ticket/table/history components
    hooks/         TanStack Query hooks
```

## A note on secrets

`backend/.env` is intentionally **git-ignored** and never committed — it holds a live Trinity API token and your session-signing secret, both of which grant real access to production support-ticket data if leaked. `backend/.env.example` is the checked-in template; copy it and fill in your own values as above. If you're forking this to use yourself, do the same — don't reuse someone else's token.

## Troubleshooting

- **"Internal Server Error" on login** — usually means the backend can't reach Trinity or the DB is locked by a leftover process; check the backend terminal output for the actual exception.
- **A ticket isn't found by number** — the app does a full paginated fallback scan if Trinity's search API doesn't surface it directly, so this should be rare; if it persists, confirm the ticket number and that your Trinity token has access to it.
- **OTP email never arrives** — leave `SMTP_*` blank in `.env` and the OTP will be shown directly on the login screen instead.
