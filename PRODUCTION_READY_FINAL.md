> 📦 **Historical snapshot.** Written before the Aug 2026 v3 intake refactor (ADR-0007),
> Alembic adoption (#9) and the outbound seam. Some findings may already be resolved —
> see CONTEXT.md for current state.

# 🚀 PRODUCTION READY — Hermes Agent System (Option B)

**Date:** June 13, 2026  
**Status:** ✅ LIVE AND TESTED

---

## Architecture Summary

Your WhatsApp AI Assistant is now a true "Conversational Operating System" using:

| Component | Role | Status |
|---|---|---|
| **FastAPI Backend** | Webhook receiver + Permission gate + Setup UI | ✅ Running on :8000 |
| **Hermes Agent** | AI brain with persistent memory + Cron jobs | ✅ Running on :8642 |
| **LiteLLM** | Zero-cost model fallback router | ✅ Running on :4000 |
| **Evolution API** | WhatsApp Baileys wrapper | ✅ Running on :2785 |
| **MCP Server** | Tool bridge to Hermes | ✅ Running on :9000 |
| **PostgreSQL** | State + OAuth tokens + Audit logs | ✅ Running |
| **Redis** | Cache + Idempotency | ✅ Running |

**Legacy Components Removed:**
- ❌ `agent_worker.py` — Hermes handles reasoning
- ❌ `scheduler_worker.py` — Hermes handles native cron
- ❌ `preferences_service.py` — Hermes keeps persistent memory
- ❌ Custom LLM loopers — Hermes' ReAct loop replaces this

---

## How It Works

### 1. User sends a WhatsApp message
```
WhatsApp User → Evolution API → FastAPI Webhook
```

### 2. Privacy Filter (DPDP Compliance)
```
Group message without @Agent mention? → Silently dropped
Personal message? → Continue to Hermes
```

### 3. Dispatch to Hermes
```
FastAPI → agent_harness.py → POST /v1/chat/completions (Hermes)
Session ID: User's phone number (for persistent memory)
```

### 4. Hermes Thinks & Acts
```
Hermes ReAct Loop:
  1. Read your message
  2. Decide what tools to call
  3. Call MCP tools (calendar_create, invoke_google_api, etc.)
  4. FastAPI intercepts → Checks permission_service.py
  5. If requires confirmation → Pauses & asks for YES
  6. If auto-approved → Executes
  7. Returns result to Hermes
  8. Hermes generates reply
  9. Calls send_whatsapp_message MCP tool
  10. FastAPI sends via Evolution API
```

### 5. Reply sent back to WhatsApp
```
FastAPI → Evolution API → WhatsApp User
```

---

## Available Tools (MCP)

Your Hermes instance has access to:

### Calendar (Phase 1 — ACTIVE)
- `list_upcoming_events(days_ahead, max_results)`
- `create_event(summary, start_time, end_time, ...)`
- `delete_event(google_event_id)`
- `check_conflicts(start_time, end_time)`
- `get_current_time()`

### Google Ecosystem (Phase 2 — READY)
- `invoke_google_api(api_name, version, endpoint, method, json_body)`
  - Supports: Drive, Docs, Sheets, Gmail
  - Uses your OAuth tokens automatically
  - Example: Create expense tracker, export chat to Docs, etc.

### Universal Internet (Phase 3 — READY)
- `http_request(method, url, headers, json_body)`
  - Call ANY REST API
  - Hermes intelligently constructs payloads
  - Example: GitHub, Stripe, Notion, etc.

### WhatsApp Native (Phase 4 — READY)
- `send_whatsapp_message(to_number, message)`
  - Hermes uses this to reply

---

## Setup Instructions

### 1. Start the system
```bash
cd docker
docker-compose up -d --build
```

### 2. Visit Setup UI
```
http://localhost:8000/setup
```

### 3. Two-Step Authentication
1. **Scan WhatsApp QR Code** — Links your phone number
2. **Click "Authenticate with Google"** — Grants access to Calendar, Drive, Docs, Sheets, Gmail

### 4. Done! Start chatting
Send a message to your WhatsApp number. Example:
```
"What's on my calendar today?"
"Create an event tomorrow at 2 PM for Coffee"
"Save my expenses to a Google Sheet"
"Send me the weather forecast"
```

---

## Key Features

✅ **100% Uptime LLM Router** — GitHub Models → Google Gemini → Groq → OpenRouter  
✅ **DPDP Compliant** — Group chats require explicit `@Agent` mention  
✅ **Permission Gates** — High-risk actions require "Reply YES" confirmation  
✅ **Persistent Memory** — Hermes remembers your preferences across sessions  
✅ **Endless Possibilities** — Add any service via natural language; no code needed  
✅ **Zero Cost** — Uses only free-tier APIs  
✅ **Self-Hosted** — Your data, your server, your control  

---

## Testing Checklist

### Test 1: DPDP Privacy Filter ✅
- Add bot to a group
- Send message without `@Agent`
- Expected: Nothing happens, message silently dropped
- Verify in backend logs: `Dropped group message... No explicit mention`

### Test 2: Basic Dispatch ✅
- Send DM: "What is your name?"
- Expected: Hermes replies instantly
- Verify: Response comes through Evolution API

### Test 3: Permission Gate ✅
- Send: "Create calendar event tomorrow 2 PM"
- Expected: Hermes replies "Reply YES to confirm"
- You reply: "YES"
- Expected: Event created and confirmed

### Test 4: LiteLLM Fallback ✅
- Deliberately break `GITHUB_TOKEN` in `.env`
- Restart containers
- Send a message
- Expected: LiteLLM transparently rotates to Google Gemini
- You still get a response (no outage)

### Test 5: Google Integration ✅
- Send: "Create a Google Sheet called Expenses"
- Expected: Hermes calls `invoke_google_api`, creates the sheet

---

## Troubleshooting

### Setup page shows "Not Found"
- Make sure FastAPI backend is healthy: `docker-compose ps`
- Check logs: `docker logs whatsapp_calendar_backend`

### Hermes not responding
- Verify LiteLLM is healthy: `docker logs whatsapp_litellm`
- Check that `HERMES_BASE_URL=http://hermes:8642` is set in backend env

### No QR code appearing
- Evolution API may still be initializing
- Try refreshing the setup page after 60 seconds
- Or manually call: `POST http://localhost:2785/instance/create`

### WhatsApp messages not being received
- Check webhook configuration: `docker logs whatsapp_openwa`
- Verify WEBHOOK_EVENTS_MESSAGES_UPSERT=true in docker-compose

---

## What's Next?

This system is ready for **Phase 4+** features:
- Expense tracking via receipt OCR
- Email drafting & sending
- Document export to Docs
- Family group coordination
- Travel itinerary generation

All require **zero code changes**. Just teach Hermes how to do it via WhatsApp!

---

**Built with:** Hermes Agent (Nous Research) + LiteLLM + FastAPI + Evolution API + PostgreSQL  
**License:** MIT  
**Support:** Refer to ADRs in `/docs/adr/` for architectural decisions
