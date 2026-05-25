# WhatsApp Calendar Agent

WhatsApp Calendar Agent is a FastAPI backend that lets a user manage Google Calendar through WhatsApp messages. It combines a WhatsApp webhook, an LLM-backed agent, Google Calendar tools, Redis session memory, PostgreSQL persistence, and background workers for reminders and summaries.

This repo is a backend-first project. It is not a hosted SaaS product yet.

## What It Does

- Receives WhatsApp Cloud API messages through a FastAPI webhook
- Uses an agent layer to decide which calendar tool to call
- Creates, reads, updates, deletes, and searches Google Calendar events
- Stores users, OAuth state, cached events, audit records, and reminders in PostgreSQL
- Uses Redis for conversation memory, OAuth cache, queues, and rate limiting
- Runs separate worker processes for agent handling and scheduled notifications
- Exposes health endpoints and a WebSocket log viewer for local debugging

## Architecture

```text
WhatsApp User
  -> WhatsApp Cloud API
  -> FastAPI webhook
  -> Message router
  -> Agent engine
  -> Calendar tools
  -> Google Calendar API

Supporting services:
  - PostgreSQL for persistent data
  - Redis for cache, sessions, and queues
  - Agent worker for async message processing
  - Scheduler worker for reminders and summaries
```

## Tech Stack

| Area | Tools |
| --- | --- |
| API | FastAPI, Uvicorn, Pydantic |
| Agent | GitHub Models or local Ollama provider |
| Calendar | Google Calendar API, OAuth 2.0 |
| Messaging | WhatsApp Cloud API |
| Data | PostgreSQL, SQLAlchemy, Alembic |
| Cache and queues | Redis |
| Runtime | Docker Compose |
| Testing | Pytest, pytest-asyncio |

## Repository Layout

```text
backend/
  app/
    api/          FastAPI routes for webhooks, OAuth, health, logs
    core/         settings, logging, retry, security, circuit breaker
    db/           PostgreSQL and Redis clients
    models/       SQLAlchemy models
    services/     agent, calendar, OAuth, WhatsApp, scheduler logic
    tools/        callable calendar tools used by the agent
    workers/      agent and scheduler worker entry points
  migrations/     SQL migrations
  tests/          pytest suite
docker/
  docker-compose.yml
scripts/
  helper scripts and manual test flows
```

## Local Setup

### Prerequisites

- Python 3.11+
- Docker Desktop
- A Meta developer app with WhatsApp Cloud API access
- A Google Cloud project with Calendar API enabled
- A GitHub Models token or a local Ollama model

### Configure Environment

Copy the example environment file:

```powershell
Copy-Item backend\.env.example backend\.env
```

Fill in these values in `backend/.env`:

```env
BASE_URL=https://your-public-dev-url.example

POSTGRES_USER=calendaruser
POSTGRES_PASSWORD=replace_with_a_local_password
POSTGRES_DB=calendar_agent

REDIS_PASSWORD=replace_with_a_local_password

USE_GITHUB_MODELS=true
GITHUB_TOKEN=replace_with_your_token
GITHUB_MODEL=gpt-4o-mini

WHATSAPP_TOKEN=replace_with_meta_token
WHATSAPP_PHONE_ID=replace_with_phone_number_id
WHATSAPP_VERIFY_TOKEN=replace_with_webhook_verify_token
WHATSAPP_APP_SECRET=replace_with_app_secret

GOOGLE_CLIENT_ID=replace_with_google_client_id
GOOGLE_CLIENT_SECRET=replace_with_google_client_secret
GOOGLE_REDIRECT_URI=https://your-public-dev-url.example/oauth/callback
```

Do not commit `backend/.env`. The repository already ignores environment files and token storage.

### Start Services

```powershell
Set-Location docker
docker compose up -d
```

Check the API:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/health/detailed
```

Open API docs locally:

```text
http://localhost:8000/docs
```

## Development

Run the backend without Docker:

```powershell
Set-Location backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run tests:

```powershell
Set-Location backend
pytest
```

Useful Docker commands:

```powershell
docker compose up -d
docker compose logs -f backend
docker compose down
```

## Security Notes

- Use placeholder values in documentation and example files.
- Keep real tokens only in `backend/.env` or a secret manager.
- Rotate credentials if they were ever committed or shared.
- Set `WHATSAPP_APP_SECRET` outside local experiments so webhook signatures can be verified.
- Replace default database and Redis passwords before any shared deployment.
- Use HTTPS for public webhook and OAuth callback URLs.

## Current Status

Working areas:

- FastAPI app structure
- WhatsApp webhook handling
- Google OAuth and Calendar service code
- Redis and PostgreSQL integration
- Agent and scheduler worker structure
- Health checks and test files

Needs hardening before production:

- Deployment guide for a real cloud target
- CI pipeline
- Broader integration tests against mocked external APIs
- Secret rotation checklist
- Load testing and operational runbook

## Related Docs

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)
- [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md)
- [LIMITATIONS_AND_IMPROVEMENTS.md](LIMITATIONS_AND_IMPROVEMENTS.md)
- [TECH_STACK_ANALYSIS.md](TECH_STACK_ANALYSIS.md)
- [REDIS_ANALYSIS.md](REDIS_ANALYSIS.md)
