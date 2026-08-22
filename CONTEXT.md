# omniWA — Current Architecture (v3.0)

## System Status

**Last Updated**: 2026-08-21  
**Deployment**: Docker Compose (5 services: postgres, redis, hermes, backend, tunnel)  
**Domain**: https://api.narendar.tech (Cloudflare tunnel, profile-gated; requires TUNNEL_TOKEN in docker/.env)  
**Queue**: In-memory per-chat async queue with Redis-backed sliding-window rate limiting  
**DB**: PostgreSQL (tenants, dashboard_users, customer_google_tokens, users, events, reminders, audit logs, preferences, ACLs)  
**Auth**: Multi-tenant — argon2 dashboard login, Redis sessions (instant revoke), per-tenant Google token isolation

---

## Architecture Overview

```
WhatsApp User
                    ↓
    Hermes Native Baileys Bridge (8642)
    [config gate: dm_policy=allowlist + require_mention]
                    ↓
    omniWA Thin Inbound Layer (8000)
    [rate-limit 20/min + per-chat queue + permission cascade]
    · owner/authorized → run
    · stranger → hold → owner approves via <CODE> yes
                    ↓
    dispatch_to_hermes (X-Hermes-Session-Id = chat target)
                    ↓
    Hermes Brain — N tenant profiles, native tools
    [Calendar, Drive, Docs, Sheets, Gmail, web; provider fallback chain]
                    ↓
    Reply delivered by Hermes bridge directly
```

**Multi-tenancy**: one Hermes process, one profile per tenant. Google tokens live encrypted in Postgres (`customer_google_tokens`), never as a shared file — prevents cross-tenant Workspace leaks.

**Dropped vs v2.1**: Evolution API/openwa, LiteLLM, MCP server, whisper, kokoro (see ADR-0007).
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
```

---

## Key Files & Structure

### Core backend API Files:
- [webhooks.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/webhooks.py) — Inbound orchestrator: rate limiting, sequential queuing workers, permission cascade, quoted-reply context parsing.
- [setup.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/setup.py) — Multi-tenant login (`dashboard_users` + argon2), tenant-aware Google connection status, system-status host metrics, onboarding screens, preferences.
- [core/auth.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/core/auth.py) — Redis-backed tenant-scoped dashboard sessions (instant revocation).
- [permissions.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/permissions.py) — Secured router executing concurrent whatsapp contact name lookups, 1+ character autocomplete contact search, and on-demand contact synchronization.
- [oauth.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/api/oauth.py) — One-click "Connect Google" flow: PKCE + tenant_id in Redis state, callback writes encrypted `CustomerGoogleToken`.
- [services/permission_service.py](file:///c:/Users/golla/Documents/Projects/whatsapp%20agent/Autonomous-Whatsapp-Agent/backend/app/services/permission_service.py) — The moat: `decide()` cascade (owner runs / authorized runs / stranger held + owner approval) and action-level PendingDecision approvals.

---

## Permission System & Authentication

### Security Credentials:
1. **Dashboard Authentication (multi-tenant)**: Accessing `/dashboard`, `/setup`, or admin REST routes requires a valid `omniwa_session` cookie. Login posts email + password against `dashboard_users` (argon2id hashes); a successful login stores `tenant_id:dashboard_user_id` in Redis under `dash_session:{sid}` (TTL 24h). Logout deletes the key — instant revocation. Legacy `ADMIN_PASSWORD` + `naru_session` remains as fallback only while no dashboard users exist.
2. **Tenant isolation**: every dashboard query resolves the tenant via the session (`core/auth.get_principal`) and filters by `tenant_id`. Google tokens are encrypted per row (`customer_google_tokens`).
3. **Access Control Lists (WhatsApp side)**:
   - **Owner** (`is_owner=true`): Approves contacts, modifies Preferences, links Google.
   - **Authorized User** (`has_permission=true`): Granted access rights by the owner. Can chat in DMs and trigger mentions in groups.
   - **Stranger** (`has_permission=false`): Message is **held** — PendingDecision created, owner receives an approval prompt; stranger sees "forwarded to owner" reply. Owner approves by replying `<CODE> yes`.
4. **Group Privacy Layer (owner-data leak prevention)** — enforced at BOTH hops:
   - **Hermes side** (`hermes-plugin/omniwa-group-privacy`, deployed to `/opt/data/plugins/`, enabled in config): with `HERMES_OWNS_WHATSAPP=true` the Baileys bridge generates AND delivers replies itself, so this is the primary guard. A `pre_llm_call` hook injects the GROUP PRIVACY MODE directive into group turns only; a `transform_llm_output` hook scrubs emails/phones/long numeric tokens from final group-bound replies. Group detection via gateway session contextvars (`@g.us` chat id). DMs, CLI, cron untouched.
   - **Backend side** (`services/group_privacy_service.py`, wired in `agent_harness.dispatch_to_hermes`): same directive + redaction for the legacy dispatch path.
   - **Display hardening**: WhatsApp platform has `tool_progress: false` and `streaming: false` in Hermes config, so groups never see tool-name bubbles or live-edited partial (pre-scrub) replies.

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
