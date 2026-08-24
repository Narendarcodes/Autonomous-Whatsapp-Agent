# omniWA — Current Architecture (v2.1)

## Design Decisions In Flight

### Outbound WhatsApp Seam (implemented 2026-08-24 — review candidate 2)
One port: `WhatsAppOutbound.send(chat_id, text, session_hint=None) -> DeliveryResult`.

1. **Selection rules consolidated** in OutboundRouter: inbound hint `agent-session`
   → agent adapter; owner `bot_mode == dual_number` → agent adapter; otherwise primary.
2. **Failures are values** — the seam never raises; adapters contain their errors.
3. **Hermes bridge stays a direct dependency** for policy-chosen system
   notifications (permission/setup notices) — those target the bridge regardless
   of session by design.
4. **Brain-failure fallback (#8)**: MessagePipeline sends FALLBACK_REPLY when
   dispatch_to_hermes returns None after one retry; ACL/quiet-hour-dropped
   chats never reach dispatch, so fallback cannot spam them.

### Message Intake Module (implemented 2026-08-24 — ADR-0007)
The webhook pipeline consolidates behind one deep module (`Inbox`) with interface
`accept(msg: InboundMessage) -> Ack`. Decisions locked during review:

1. **Seam placement** — HMAC signature verification stays at the HTTP edge (router).
   Evolution payload *parsing/normalization* also lives in the edge adapter; the
   module only ever sees a trusted, normalized `InboundMessage`.
2. **Durable queue** — per-chat asyncio queues are replaced by a Redis Streams
   consumer group behind the seam (resolves issue #6; absorbs the orphaned stream
   helpers from redis_client.py). Messages survive restarts; multi-worker safe.
3. **Ack semantics** — `Ack` is an opaque admission enum (accepted · duplicate ·
   rate_limited · rejected_queue_full · ignored). It describes **admission only**, never
   delivery. Post-admission gates (DPDP, ACL, quiet hours, commands, dispatch)
   run asynchronously inside the module and their outcomes appear nowhere in Ack.
4. Gate order is load-bearing and frozen inside the module:
   idempotency → rate limit → loop guard → queue cap ‖ (async) DPDP → owner
   resolution → ACL/quiet hours → command/approval intercepts → dispatch.
5. **Consumption model** (agreed): one consumer, sequential per chat; consumer-group
   mechanics retained so chat-hash partitioning stays a config-level change.
   Constraint until then: single backend replica. Crash-safe PENDING re-claim on boot
   gives restart survival.
6. **Private stages** (agreed): `/set` commands, approval short-codes, SETUP/OAUTH
   intercepts, quoted-reply prefixing and group sender-info prefixing are invisible
   from the interface. No stage extension point (one adapter = hypothetical seam).
7. **Test surface** (agreed): new tests hit `inbox.accept()` against in-memory fakes
   (FakeStream / FakeOutbound / FakeHermes). Rate-limit test rewritten at interface
   level; dashboard/auth tests survive untouched. Full decision record:
   [ADR-0007](docs/adr/0007-message-intake-module.md).


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
2. Verify Webhook Signature: X-Evolution-Signature (HMAC-SHA256)
   ↓
3. Edge adapter normalizes payload -> trusted InboundMessage (app/intake/evolution.py)
   ↓
4. Inbox.accept(message) -> Ack          [ADR-0007 seam]
   ├─ session policy: dual_number / agent-session owner-only -> ignored
   ├─ idempotency (Redis NX, instance-scoped; content-fingerprint fallback)
   ├─ fixed-window rate limit (TTL set once per window)
   ├─ loop guard (sent_message:/sent_text: markers)
   └─ per-chat cap -> XADD omniwa:inbound  → Ack "queued"
   ↓
5. StreamConsumer (omniwa:inbound / agent_workers, sequential per chat):
   voice STT → DPDP @mention filter → user upsert (race-safe) →
   monitored-chat + trusted-sender checks → owner approvals / slash commands /
   setup intercepts → ACL + quiet hours → prompt assembly →
   dispatch_to_hermes(session=chat JID)
   ↓
6. Hermes replies via its Baileys bridge; failures retry then dead-letter
   (omniwa:inbound:dead). Restart survival: unconsumed entries are re-claimed
   from the consumer group PENDING list on boot.
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
