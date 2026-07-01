# AGENTS.md

This file gives AI coding agents the rules and context they need to be useful here. Read top to bottom on session start.

## Project

**WhatsApp AI Agent (Phase 2: Permission System)**

A self-hosted WhatsApp bot with:
- **Hermes Agent** brain (Nous Research) — native cron, ReAct loop, persistent memory
- **Permission system** — owner approves users before they can chat
- **Google workspace integration** — Calendar, Drive, Docs, Sheets, Gmail via OAuth
- **FastAPI webhook** → Hermes + MCP tools architecture

### How It Works

1. Owner scans WhatsApp QR code → links device
2. Owner authenticates with Google OAuth
3. Owner visits `/dashboard` to approve users
4. Users can now text bot → get AI responses via Hermes
5. In groups: Users @mention bot by its chosen name

### Tech Stack
- **FastAPI** — webhook receiver + setup UI
- **Hermes Agent** — AI brain (Docker container)
- **LiteLLM** — model router (fallback chain: GitHub → Gemini → Groq → OpenRouter)
- **PostgreSQL** — user data, events, audit logs
- **Redis** — session cache, QR codes, OAuth state
- **Evolution API** — WhatsApp protocol adapter
- **MCP Server** — tool definitions (calendar, drive, sheets, http_request)

## Latest Status (2026-06-13)

**Phase 2 Complete**: Permission system + bidirectional setup flow  
**All 10 containers healthy**  
**Testing in progress**: Users can text bot, receive setup prompts

See `SESSION_2026_06_13.md` for what was done this session.

## Key Files

### Entrypoints
- `backend/app/main.py` — FastAPI app with all routers
- `backend/app/api/webhooks.py` — WhatsApp message handler (REWRITTEN)
- `backend/app/api/setup.py` — Setup UI + QR endpoint
- `backend/app/api/permissions.py` — Permission management API (NEW)
- `docker/docker-compose.yml` — 10-container stack

### Services  
- `backend/app/services/whatsapp_service.py` — Evolution API client
- `backend/app/services/agent_harness.py` — Dispatch to Hermes HTTP API
- `backend/app/services/setup_service.py` — Chat-based setup (NEW)
- `backend/app/services/oauth_service.py` — Google OAuth flow
- `backend/app/mcp_server/main.py` — MCP tool definitions

### Models
- `backend/app/models/models.py` — User, EventCache, Reminder, PendingDecision (has_permission column added)

### Documentation
- `CONTEXT.md` — Architecture, domain glossary, API endpoints
- `docs/adr/` — Architecture decision records (0001-0006)
- `docs/agents/` — Agent skills & triage labels
- `SETUP_FLOW_IMPLEMENTATION.md` — Detailed setup flow
- `SETUP_TESTING_GUIDE.md` — Test scenarios
- `SESSION_2026_06_13.md` — This session's changes

## Agent Skills

### Issue Tracker
Issues and PRDs live on GitHub at `Narendarcodes/Autonomous-Whatsapp-Agent`.  
See `docs/agents/issue-tracker.md`.

### Triage Labels
Canonical labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.  
See `docs/agents/triage-labels.md`.

### Domain Docs
Single-context repo. `CONTEXT.md` and `docs/adr/` live at repo root.  
See `docs/agents/domain.md`.

## Current Workflow

When starting work on this project:

1. **Read CONTEXT.md** — Understand current architecture
2. **Check SESSION_2026_06_13.md** — What was just done
3. **Review docs/adr/** — Architecture decisions (why things are the way they are)
4. **Look at AGENTS.md** (this file) — Agent expectations
5. **Check CRITICAL_BUGS.md** if it exists — Known issues to fix
6. **Start work** — Make atomic commits, reference issues if you fix them

## Testing Checklist

Before declaring work complete, test:

- [ ] Owner can scan QR → WhatsApp linked
- [ ] Owner can OAuth → Google tokens stored in DB
- [ ] Non-owner gets "Setup mode" message (not setup prompt)
- [ ] Owner can visit `/dashboard` → sees all pending users
- [ ] Owner can grant permission → user table updated
- [ ] Authorized user texts bot → gets response from Hermes
- [ ] Group message without @bot mention → silently dropped (DPDP)
- [ ] Group message with @bot mention → Hermes responds

## Common Patterns

### Permission Check in Webhook
```python
if not is_owner and not user.has_permission:
    await whatsapp_service.send_text(phone, "🔒 Setup mode")
    return
```

### Use settings.BASE_URL
All URLs in messages should reference `settings.BASE_URL` not hardcoded domains:
```python
msg = f"Visit {settings.BASE_URL}/setup to complete setup"
```

### Async Dispatch to Hermes
```python
import asyncio
asyncio.create_task(dispatch_to_hermes(phone, message_text))
```

## Debugging

**Check webhook flow**:
```bash
docker-compose -f docker/docker-compose.yml logs -f backend
```

**Check database**:
```bash
docker-compose -f docker/docker-compose.yml exec -T postgres \
  psql -U calendaruser -d calendar_agent \
  -c "SELECT wa_phone, is_owner, has_permission FROM users"
```

**Test permission API**:
```bash
curl http://localhost:8000/permissions
curl -X POST "http://localhost:8000/permissions/grant?phone=919876543210"
```

**Watch Hermes logs**:
```bash
docker-compose -f docker/docker-compose.yml logs -f hermes
```
