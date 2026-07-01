# WhatsApp AI Agent — Current Architecture (v2.1)

## System Status

**Last Updated**: 2026-06-15  
**Deployment**: Docker Compose (8 containers, all healthy and active)  
**Domain**: https://api.narendar.tech (Fully routed via Cloudflare Tunnel container)  
**Queue**: In-memory per-chat async queue with Redis-backed sliding-window rate limiting  
**DB**: PostgreSQL (users, events, reminders, audit logs, preferences, ACLs)

---

## Architecture Overview

```
WhatsApp User → Evolution API (2785) 
                    ↓
              Webhook Receiver (8000)
                    ↓
          [Signature Check + Idempotency Filter]
                    ↓
          [Sliding-Window Rate Limiting (Redis)]
                    ↓
          [Per-Chat Sequential Queue (asyncio)]
                    ↓
          [Voice Transcription + DPDP Compliance]
                    ↓
          [ACL + Quiet Hours Evaluation]
                    ↓
          [Google Setup Flow & Command Parser]
                    ↓
          [WhatsApp Quoted Reply Context Parser]
                    ↓
          [Sequential Dispatch to Hermes Brain]
                    ↓
    Hermes Agent (8642) + MCP Server (9000)
                    ↓
       [Tools: Calendar, Drive, Docs, Sheets, Gmail, HTTP]
                    ↓
          LiteLLM Router (4000)
                    ↓
     [Model Fallback Chain: GitHub → Gemini → Groq → OpenRouter → NIM]
```

---

## Key Files & Structure

### Core backend API Files:
- [webhooks.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/webhooks.py) — Webhook orchestrator handling signatures, idempotency, rate limiting, sequential queuing workers, WhatsApp quoted reply context parsing, and `/webhook/agent` / `/webhook/agent-qr` callback routing.
- [setup.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/setup.py) — Configures system-status host metrics (Windows compatible), onboarding screens, default preference queries, time string validation, admin authentication views (`/login`, `/logout`), agent setup cancellation, and agent session connection status retrieval.
- [permissions.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/permissions.py) — Secured router executing concurrent whatsapp contact name lookups, 1+ character autocomplete contact search, and on-demand contact synchronization.
- [oauth.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/oauth.py) — Handles calendar linking, securing authorize/start endpoints, and returning themed HTML error responses on state expiry.
- [whatsapp_service.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/services/whatsapp_service.py) — Handles basic WhatsApp message operations and integrates routing for outgoing agent replies in `dual_number` mode.
- [test_endpoints.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/tests/test_endpoints.py) — Automated integration tests covering security, rate limits, logins, webhook message parsing, and whitelist resets.

---

## Permission System & Authentication

### Security Credentials:
1. **Admin Console Authentication**: Accessing `/dashboard`, `/setup`, or any admin REST routes (`/api/*`, `/permissions/*`, `/oauth/start`, `/oauth/authorize`) requires a valid `naru_session` session cookie. This cookie matches an active authenticated session stored inside Redis, generated via a credential check against `ADMIN_PASSWORD` on `/login`.
2. **Access Control Lists (ACL)**:
   - **Owner** (`is_owner=true`): Whitelists contacts, modifies Preferences, and links Google.
   - **Authorized User** (`has_permission=true`): Granted access rights by the owner. Can chat in DMs and trigger mentions in groups.
   - **Pending User** (`has_permission=false`): Dropped silently at the webhook layer (unless requesting OAuth setup status).

---

## Detailed Message Flow

```
1. Evolution API pushes WhatsApp event to POST /webhook/openwa
   ↓
2. Verify Webhook Signature: Check X-Evolution-Signature (HMAC-SHA256) header
   ↓
3. Check Webhook Idempotency: Reject duplicates using event message ID inside Redis (TTL: 24h)
   ↓
4. Parse Evolution Event: Detect remote JID, text body, and check if it is a self-chat message
   ↓
5. Check Loop Prevention: Drop messages that originate from our own Bot API senders
   ↓
6. Sliding Window Rate Limiting: Assert sender requests do not exceed 20 / min via Redis `rl:{sender}`
   ↓
7. Per-Chat Queueing:
   - Match message to chat_id Queue.
   - If no Queue exists, launch worker task `_chat_worker(chat_id, queue)`.
   - Drop messages and send a warning if queue length exceeds 5 (anti-spam buffer).
   - Put message in Queue and return HTTP 200 {"status": "queued"} immediately.
   ↓
8. Queue Worker consumes message:
   - Transcribe base64 voice recordings (if is_audio) using Groq/Whisper.
   - DPDP Compliance Check: Drop group messages lacking explicit agent mentions.
   - Retrieve / create database User entity.
   - Owner Slash Command Check: Intercept and process commands (e.g. /configure).
   - Setup Flow Interceptor: Direct owner to complete Calendar linking if missing.
   - ACL Check: Drop if contact is blocked or logs silently during active Quiet Hours.
   - Quoted Reply Context Check: Extract contextInfo -> quotedMessage, prefixing the text bubble.
   - Sequential Dispatch: Call dispatch_to_hermes(sender_phone, finalized_prompt).
   ↓
9. dispatch_to_hermes posts message:
   - Posts to Hermes Agent (8642) passing session-id (phone number) for memory mapping.
   - Extracts the LLM choices text result and sends it back to the user via WhatsApp.
```

---

## Container Stack (docker-compose.yml)

| Service | Port | Description |
| :--- | :--- | :--- |
| **postgres** | 5432 | Database backend saving users, preferences, ACL configs, and logs. |
| **redis** | 6379 | Keeps session states, idempotency hashes, rate limit counters, and status QR data. |
| **litellm** | 4000 | LiteLLM routing layer enabling fallbacks (GitHub -> Gemini -> Groq). |
| **hermes** | 8642 | Nous Research ReAct agent loop (native memory & scheduling). |
| **mcp-server** | 9000 | FastMCP tool bindings (Google Calendar, Drive, search, text-to-speech). |
| **openwa** | 2785 | Baileys-based Evolution API protocol wrapper for WhatsApp connection. |
| **backend** | 8000 | FastAPI webhook receiver, admin endpoints, and user management UI. |
| **tunnel** | N/A | Cloudflare tunnel routing localhost traffic securely to api.narendar.tech. |

---

## Testing Verification Checklist

Run python integration test suites:
```bash
docker compose -f docker/docker-compose.yml exec -T backend python -m pytest -x --tb=short
```

- [x] Unauthenticated dashboard routes redirect to `/login`
- [x] Correct password input returns session cookie & authorizes `/dashboard`
- [x] Inbound webhook requests reject invalid HMAC signatures
- [x] Message spamming triggers sliding-window rate limiting in Redis
- [x] Multi-message fire is queued per-chat and processed sequentially
- [x] WhatsApp quoted message bubbles are parsed and context is prepended
- [x] Invalid quiet hours time inputs trigger Pydantic schema validation errors
- [x] Expired OAuth states return user-friendly glassmorphic HTML error pages
- [x] Permission whitelists resolve user display names concurrently via `asyncio.gather`
- [x] Endpoint `/permissions/reset` deletes non-owner and non-agent contacts correctly
- [x] Agent setup cancellation preserves existing configurations during active replacements
- [x] Outbound replies in `dual_number` mode route through the connected agent instance
- [x] Autocomplete search threshold supports 1+ characters (frontend and backend)
- [x] "Refresh Contacts" on-demand synchronization updates Redis cache from Baileys database
- [x] Autocomplete UI employs 300ms debouncing, AbortController request cancellation, and local cache
- [x] Agent session connection state monitored dynamically (`GET /api/agent/status`) with dashboard banner alerts
- [x] Pytest-asyncio event loop teardown handles closed connections gracefully
