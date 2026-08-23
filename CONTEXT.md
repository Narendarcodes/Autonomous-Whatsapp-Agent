# omniWA — Current Architecture (v3.0)

## System Status

**Last Updated**: 2026-08-23  
**Deployment**: Docker Compose (5 services: postgres, redis, hermes, backend, tunnel)  
**Domain**: https://api.narendar.tech (Cloudflare tunnel, profile-gated; requires TUNNEL_TOKEN in docker/.env)  
**WhatsApp transport**: Hermes-native Baileys bridge (in-image, port 8642) — owns pairing, inbound filtering and reply delivery  
**DB**: PostgreSQL (tenants, dashboard_users, customer_google_tokens, users, events, reminders, audit logs, preferences, ACLs)  
**Auth**: Multi-tenant — argon2 dashboard login, Redis sessions (instant revoke), per-tenant Google token isolation

**Dropped vs v2.1** (ADR-0007): Evolution API/openwa, LiteLLM, MCP server, whisper, kokoro.  
**Also removed since**: `/webhook/openwa|/webhook/qr` receivers, `whatsapp_service` Evolution client, `agent_instance_service`, dual_number flows (commit 368fa7d).

---

## Architecture Overview

```
WhatsApp User
     ↓
Hermes Native Baileys Bridge (:8642, inside hermes container)
[allowlist + dm/group policy + require_mention gate]
     ↓
Hermes Gateway — one session per chat target
[X-Hermes-Session-Id = chat JID/phone]
     ↓
Hermes Brain — N tenant profiles, native tools
[Calendar, Drive, Docs, Sheets, Gmail, web]
     ↓
Reply delivered by the bridge directly to the same chat

omniWA Backend (:8000) — control plane, NOT in the message path:
  · Dashboard UI + auth (argon2, Redis sessions)
  · WhatsApp pairing flow (QR display, status, disconnect)
  · Bridge configuration (mode/policies via /api/pairing/bridge)
  · Google OAuth connect (PKCE, per-tenant encrypted tokens)
  · Permissions/ACL management, preferences, API keys
  · Owner notifications & approval prompts via bridge POST /send
```

In v3 **no WhatsApp message transits the backend.** Inbound goes bridge → gateway
session directly; the old webhook guard pipeline (rate limit → queue → cascade)
was Evolution-era and has been deleted. Strangers are stopped at the bridge
allowlist instead of being "held" by the backend.

**Multi-tenancy**: one Hermes process, one profile per tenant. Google tokens live
encrypted in Postgres (`customer_google_tokens`), never as a shared file.

---

## Key Files & Structure

### Backend API
- [setup.py](backend/app/api/setup.py) — Multi-tenant login (`dashboard_users` + argon2), preferences, system-status host metrics, **disconnect** (deletes Baileys session dir on shared volume + restarts hermes), API keys CRUD.
- [whatsapp_pairing.py](backend/app/api/whatsapp_pairing.py) — Pairing status/QR (reads session files on the shared volume), plus `GET/PUT /api/pairing/bridge`: runtime bridge config (mode `self-chat|bot`, `dm_policy`, `group_policy`, `require_mention`, `allow_from`) written into the shared volume + hermes restart via docker.sock.
- [permissions.py](backend/app/api/permissions.py) — Secured router for users/trust/ACL management; contact search serves the Redis cache only (v3 has no live directory source).
- [oauth.py](backend/app/api/oauth.py) — One-click "Connect Google": PKCE + tenant_id in Redis state, callback writes encrypted `CustomerGoogleToken`.
- [health.py](backend/app/api/health.py) — `/health/detailed` reports postgres, redis, `whatsapp_bridge` (GET bridge /health) and hermes.

### Services
- [bridge_client.py](backend/app/services/bridge_client.py) — Hermes bridge HTTP client: `send_text(chat_id, msg)` → `POST /send {chatId, message}` with retry/backoff; `bridge_status()` → `GET /health`. Used for backend-originated DMs (approval notices, setup prompts).
- [bridge_config_service.py](backend/app/services/bridge_config_service.py) — Reads/writes runtime bridge files on the shared volume (`bridge_env` sourced at hermes boot; `config.yaml` whatsapp policies) and restarts the hermes container over the docker socket.
- [whatsapp_pairing_service.py](backend/app/services/whatsapp_pairing_service.py) — Shared-volume paths (`creds.json`, QR state) powering the dashboard pairing tab.
- [agent_harness.py](backend/app/services/agent_harness.py) — Legacy dispatch helper (`dispatch_to_hermes`) kept for programmatic use: builds the omniWA system context, injects group-privacy directive for `@g.us` targets, redacts group-bound content (`_finalize_reply`). Not part of the live inbound path.
- [phone_utils.py](backend/app/services/phone_utils.py) — libphonenumber validation/E.164 normalisation (ex-Evolution client).
- [permission_service.py](backend/app/services/permission_service.py) — Action-level PendingDecision approvals (`<CODE> yes/no`); owner notification delivered via `bridge_client`. The inbound stranger-hold cascade is dormant since messages no longer transit the backend.
- [group_privacy_service.py](backend/app/services/group_privacy_service.py) — Directive builder + regex redaction shared with the harness.

### Hermes side
- `hermes-plugin/SPIRIT_SOUL_OMNIWA.md` — canonical soul, deployed to `/opt/data/SOUL.md` in the container.
- `hermes-plugin/omniwa-group-privacy` — deployed to `/opt/data/plugins/`: `pre_llm_call` hook injects the GROUP PRIVACY MODE directive into group turns; `transform_llm_output` scrubs emails/phones/long numerics from group-bound replies. This is the primary privacy guard at the true egress point.

---

## Permission System & Authentication

### Security Credentials:
1. **Dashboard Authentication (multi-tenant)**: `/dashboard`, `/setup`, admin REST routes require an `omniwa_session` cookie; login posts email + password against `dashboard_users` (argon2id); session stored in Redis under `dash_session:{sid}` (TTL 24h); logout deletes the key. Legacy `ADMIN_PASSWORD` + `naru_session` remains only while no dashboard users exist.
2. **Tenant isolation**: every dashboard query resolves the tenant via the session (`core/auth.get_principal`) and filters by `tenant_id`; Google tokens encrypted per row.
3. **Access Control Lists (WhatsApp side)**:
   - **Owner** (`is_owner=true`): manages permissions, preferences, Google link, pairing.
   - **Authorized User** (`has_permission=true`): may chat in DMs and trigger mentions in groups — *provided* their number/LID is in the bridge allowlist.
   - **Stranger**: blocked at the bridge (`dm_policy=allowlist`). To grant access, the owner adds them via the Permissions page **and** ensures the number/LID appears in `allow_from` (`PUT /api/pairing/bridge` or the dashboard Connection Mode card).
4. **Group Privacy Layer** — enforced Hermes-side by the `omniwa-group-privacy` plugin (directive injection + output scrubbing on group turns only; DMs, CLI, cron untouched). Display hardening: `tool_progress: false`, `streaming: false` so groups never see tool bubbles or partial pre-scrub replies. The backend mirror of these layers lives in `agent_harness` for any backend-dispatched reply.
5. **Phone Numbers & Roles** — neither WhatsApp number is intrinsically a "bot": roles are configuration, not identity. `OWNER_WA_PHONE` feeds owner detection in OAuth/ACL flows. A dedicated bot number may be acquired later for bot mode; never assume which SIM scanned a pairing QR from the number alone.

---

## Runtime WhatsApp Configuration

Single source of truth: `backend/.env` → `WHATSAPP_*` keys (compose `env_file`),
plus runtime overrides written by the backend onto the shared `hermes_data`
volume (`bridge_env` sourced by the hermes container command; policy block under
`whatsapp:` in `config.yaml`). Effective order: config.yaml > env defaults.

- Switch self-chat ↔ bot: dashboard Identity tab card, or `PUT /api/pairing/bridge` (restarts hermes; pairing session survives).
- Disconnect/re-pair: dashboard WhatsApp tab (deletes session dir + restarts; fresh QR).
- Allowlist entries include LIDs (e.g. `200283032441063@lid`) — always check `wa_block.yaml`/effective allowlist when debugging delivery.

---

## Container Stack (docker-compose.yml)

| Service | Port | Description |
| :--- | :--- | :--- |
| **postgres** | 5432 | Database backend saving users, preferences, ACL configs, audit logs. |
| **redis** | 6379 | Dashboard sessions, rate counters, caches (contacts, QR, connection state). |
| **hermes** | 8642 | Nous Research Hermes agent: gateway + OpenAI-compatible API + native Baileys bridge (owns WhatsApp transport). |
| **backend** | 8000 | FastAPI control plane: dashboard, pairing/bridge config, OAuth, permissions, health. |
| **tunnel** | N/A | Cloudflare tunnel routing api.narendar.tech to the stack. |

Shared volume `hermes_data` mounts at `/opt/data` (hermes) and `/opt/hermes_data`
(backend, rw): pairing session, SOUL.md, plugins, `bridge_env`, `config.yaml`.

---

## Testing Verification Checklist

Run the suite from `backend/` with the repo venv (system python lacks pytest):

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q
```

- [x] Full suite green (~154 tests): endpoints, pairing, bridge config/client, permission cascade/service, group-privacy wiring, phone utils
- [x] Unauthenticated dashboard routes redirect to `/login`
- [x] Bridge mode switch round-trip verified live (bot ↔ self-chat, bridge.log shows `mode:` flip)
- [x] `POST /setup/disconnect` removes the Baileys session dir and restarts hermes
- [x] `/health/detailed` reports `whatsapp_bridge: ok` + `hermes: ok`
- [x] Invalid quiet-hours inputs rejected by Pydantic schema validation
- [x] Expired OAuth states render user-friendly error pages
- [x] Group-bound replies redacted (`_finalize_reply`), DMs untouched
- [x] Contacts sync/search served cache-only (Evolution directory gone)

Backend code changes require `docker restart whatsapp_calendar_backend` to go live.

---

## Known Gaps & Future Work

### Inbound rate limiting (not enforced)
The old per-sender limiter (`check_rate_limit` in `db/redis_client.py`, 20
msgs/60s via `RATE_LIMIT_REQUESTS`/`RATE_LIMIT_WINDOW_SECONDS`) has no caller
since the webhook relay was removed; Hermes enforces nothing equivalent.
Current exposure is low because `dm_policy=allowlist` means only owner-trusted
senders can trigger the agent at all — revisit before ever setting `open`.
Caveat: the gateway's single inbound queue is shared across chats, so an
allowlisted flooder would delay responses for everyone.

**Future fix — "guard hop":** teach the Hermes gateway to consult the backend
(e.g. `POST /api/guard/check {sender}`) before dispatching a message into a
session. This would revive rate limiting *and* the stranger-hold cascade in
one move. Feasibility unknown: need to verify a Hermes platform hook can
*reject/drop* an inbound message pre-dispatch (the omniwa-group-privacy plugin
only rewrites prompts/outputs). Alternative rejected for now: a token bucket
inside `bridge.js` — that file is baked into the image and edits would not
survive container recreation.

### Root landing page (planned, not built)
`https://api.narendar.tech/` currently serves no index (bare 404). Plan:
a marketing/onboarding landing page as the default `/` view with
login/dashboard links. To be built on its own branch (`feat/landing-page`);
deliberately out of v3 QA scope (owner decision 2026-08-23).

### opencode-zen provider degradation (external)
Since ~2026-08-23 all zen models return `401 Model <empty> is not supported`
through Hermes despite model IDs existing in zen's live catalog (`GET /models`
lists them). Direct probes hit Cloudflare 1010 bot-walls. Strongly suggests an
expired/entitlement-changed API key on zen's side. Mitigation: Hermes'
fallback chain lands on openrouter, so replies still work (slower — several
failed attempts first). Fix is external: regenerate `OPENCODE_ZEN_API_KEY`
in `<hermes_data>/.env` and restart hermes.
