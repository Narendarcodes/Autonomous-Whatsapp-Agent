# OmniWA Auth & Simplified Architecture Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Replace the 10-container stack with a 2-container design (Hermes brain + tunnel), keep omniWA's FastAPI dashboard as a thin client, and build a real multi-user authentication + authorization system (web login, per-customer Google OAuth, owner permission cascade) — the product moat.

**Architecture:** Hermes Agent (native WhatsApp/Baileys bridge, Google Workspace skill, cron, fallback providers) becomes the engine. omniWA's FastAPI app shrinks to: a web dashboard with session auth, a customer-facing Google "Connect" OAuth flow (web client type), a message-interception/permission layer, and a Postgres/Redis store for users + tokens. Engine calls happen via Hermes HTTP API (port 8642).

**Tech Stack:** FastAPI, PostgreSQL (async SQLAlchemy), Redis (sessions/OAuth state), Hermes Agent (gateway + `:8642` HTTP API), Google OAuth2 (web client → `https://api.narendar.tech/oauth/callback`), JWT or signed-cookie sessions, bcrypt/argon2 for dashboard passwords, `python-jose`/`passlib`.

---

## 1. Current Context (verified)

- Repo: `Autonomous-Whatsapp-Agent` at `C:\Users\golla\Documents\Projects\whatsapp agent\Autonomous-Whatsapp-Agent`.
- Current stack: 8 default Docker services (postgres, redis, litellm, hermes, mcp-server, evolution-api, backend, tunnel) + 2 optional audio (whisper, kokoro). Target: backend + hermes (+ postgres + redis + tunnel as supporting).
- **WhatsApp pairing VERIFIED WORKING** via Hermes native Baileys bridge (`hermes whatsapp` → self-chat mode). Session at `~/AppData/Local/hermes/whatsapp/session/creds.json`, owner approved as `13349261734098` ("Narendar").
- **Hermes Google Workspace skill**: works as a *developer* OAuth (Desktop app type + `localhost` redirect). Customer-facing OAuth must be omniWA's own `web` client (already created: `google_client_secret.json` with redirect `https://api.narendar.tech/oauth/callback`).
- Current dashboard auth is a SINGLE shared `ADMIN_PASSWORD` (see `backend/app/api/setup.py:27-70`). Not multi-user safe.
- Existing skeleton files: `backend/app/api/oauth.py` (Google OAuth authorize/callback), `backend/app/services/oauth_service.py` (`build_authorization_url`, `exchange_code_for_tokens`, `store_user_credentials`), `backend/app/services/permission_service.py`, `backend/app/core/security.py` (`decrypt_token`), `backend/app/models/models.py` (`User`, `ApiKey`, `AuditLog`, `PendingDecision`).

## 2. Locked Decisions (from earlier discussion)

1. **Engine integration = HTTP API** (FastAPI calls Hermes `:8642`), not Python library, not dropping FastAPI.
2. **Dashboard is a product feature** — kept, slimmed to a thin client.
3. **Public URL (Cloudflare tunnel) needed** for dashboard management.
4. **Customer Google OAuth = omniWA's own `web` client**, one-click "Connect Google" → Allow → done. Hermes' OAuth is for developer testing only.
5. **Permission cascade is the moat**: owner acts instantly; non-owner/group requests are held and routed to owner for approve/deny. Not native to Hermes.
6. **Language**: English primary (per earlier localization note, default to English, regional later).

## 3. Container & File Survival Map

### Containers: 10 → 2

| Container | Disposition | Rationale |
|---|---|---|
| `backend` (FastAPI) | **KEEP (slim)** | Becomes thin client: dashboard auth, OAuth, permission layer. Drops agent engine logic. |
| `hermes` | **KEEP (the brain)** | Native WhatsApp bridge, Google Workspace skill, cron, fallback providers, `:8642` HTTP API. |
| `litellm` | **DROP** | Replaced by Hermes `fallback_providers` (gemini → nvidia → custom Groq). |
| `postgres` | **KEEP** | Users, per-customer Google tokens, audit logs, permissions. |
| `redis` | **KEEP** | Sessions, OAuth state, QR cache. |
| `evolution-api` | **DROP** | Replaced by Hermes native Baileys bridge (already verified working). |
| `whisper` | **DROP** | Hermes handles STT/TTS natively. |
| `kokoro` | **DROP** | Hermes handles TTS natively. |
| `mcp` | **DROP** | Tools live inside Hermes now. |
| `frontend` | **MERGE into backend** | Dashboard served by FastAPI static/templates (already the case via `templates/`). |

### File survival (backend/app)

**KEEP (product moat):**
- `api/setup.py` → rewrite auth to multi-user session login (not single ADMIN_PASSWORD)
- `api/oauth.py` → harden: customer-facing Google `web` client flow, per-customer token storage
- `api/permissions.py` → keep + extend: owner approve/deny cascade
- `api/webhooks.py` → keep: message ingestion, but route through permission layer before Hermes
- `core/security.py` → keep: password hashing, token encrypt/decrypt
- `models/models.py` → extend: `User` (dashboard login), `CustomerGoogleToken`, `Session`
- `services/permission_service.py` → keep: the cascade engine
- `services/oauth_service.py` → keep: exchange + store, point at omniWA `web` client
- `services/database.py` → keep: Postgres async
- `db/redis_client.py` → keep
- `services/preferences_service.py` → keep

**DROP (absorbed by Hermes):**
- `services/agent_harness.py` → replaced by Hermes HTTP API client (slim wrapper kept)
- `services/litellm_service.py` → DROP
- `services/agent_instance_service.py` → DROP (no second Evolution instance)
- `services/whatsapp_service.py` → DROP (Hermes owns WhatsApp; keep only a thin event receiver if needed)
- `services/audio_service.py` → DROP (Hermes native)
- `services/calendar_service.py` → DROP (Google Workspace skill via Hermes)
- `services/connector_service.py` → DROP
- `services/docker_manager.py` → DROP (no container restarts needed; Hermes self-manages)
- `services/setup_service.py` → DROP (chat-based setup replaced by dashboard)
- `mcp_server/main.py` → DROP
- `tools/registry.py` → DROP (Hermes tools)
- `workers/` → DROP

## 4. Implementation Tasks

Tasks are bite-sized (2-5 min). Each has exact file paths, TDD steps, and verification.

### Task A1: Multi-tenant schema — `Tenant`, `CustomerGoogleToken`, `DashboardUser`
**Objective:** Schema for multi-tenant isolation: each tenant = one business with its own dashboard logins, WhatsApp number, and isolated Google tokens.

**Files:** Modify `backend/app/models/models.py`

**Step 1:** Add after `User` model:
```python
class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    whatsapp_session_ref = Column(String, nullable=True)  # Hermes wa session key
    created_at = Column(DateTime, default=datetime.utcnow)

class CustomerGoogleToken(Base):
    __tablename__ = "customer_google_tokens"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_wa_phone = Column(String, nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_token_enc = Column(Text, nullable=False)   # app-level encrypted
    refresh_token_enc = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String, nullable=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DashboardUser(Base):
    __tablename__ = "dashboard_users"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)  # unique per tenant
    password_hash = Column(String, nullable=False)
    is_owner = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_email"),)
```
Note: `User.wa_phone` gains `tenant_id` FK for isolation.

**Step 2:** `alembic revision --autogenerate -m "multi-tenant schema"` → `alembic upgrade head`.
**Step 3:** Verify `psql -c "\dt"` shows `tenants`, `customer_google_tokens`, `dashboard_users`.
**Step 4:** Add `get_current_tenant(request)` dependency resolving tenant from dashboard session. Commit.

### Task A2: Password hashing — argon2
**Objective:** Secure dashboard password storage (replace plaintext ADMIN_PASSWORD compare). Use argon2 (OWASP-recommended, GPU-resistant) — chosen for multi-tenant high-impact breach scenario.

**Files:** Modify `backend/app/core/security.py`
**Step 1 (test):** `tests/test_security.py`:
```python
def test_hash_verify():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)
```
**Step 2:** Implement using `argon2-cffi`:
```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
_ph = PasswordHasher()
def hash_password(p): return _ph.hash(p)
def verify_password(p, h):
    try: return _ph.verify(h, p)
    except VerifyMismatchError: return False
```
**Step 3:** `pytest tests/test_security.py -v` → PASS. Commit.

### Task A3: Redis-backed session auth (multi-tenant)
**Objective:** Replace single `naru_session` cookie + ADMIN_PASSWORD with tenant-scoped server-side sessions. Redis is KEPT (not dropped) — it stores sessions, OAuth state, rate-limit, QR, idempotency, contacts.

**Files:** Create `backend/app/core/auth.py`; modify `backend/app/api/setup.py` (replace lines 27-70).
**Step 1:** `core/auth.py`:
```python
from fastapi import Request, HTTPException
from app.db.redis_client import cache_get

async def get_current_tenant(request: Request) -> int:
    sid = request.cookies.get("omniwa_session")
    if not sid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    val = await cache_get(f"dash_session:{sid}")
    if not val:
        raise HTTPException(status_code=401, detail="Session expired")
    tenant_id, user_id = val.split(":")
    return int(tenant_id)   # every downstream query filters by this
```
**Step 2:** `/login` validates against `DashboardUser` (argon2), then `cache_set(f"dash_session:{sid}", f"{tenant_id}:{user_id}", ttl=86400)`, set HttpOnly+Secure+SameSite cookie.
**Step 3:** Logout = `cache_set(f"dash_session:{sid}", "", ttl=1)` (instant revoke — reason we use Redis over JWT).
**Step 4:** Seed owner: `INSERT INTO dashboard_users (tenant_id, email, password_hash, is_owner) VALUES (1, 'owner@narendar.tech', hash_password('...'), true)`.
**Step 5:** Test: wrong pw → 401; correct → 303 + cookie; logout → session dead immediately. Commit.

**Redis uses in new plan (ALL kept):** (1) dashboard sessions, (2) oauth_state:{state}→code_verifier+tenant_id, (3) rl:{sender} rate-limit 20/min, (4) whatsapp:qr_code, (5) webhook:{msg_id} idempotency 24h, (6) contact autocomplete cache. JWT is NOT used.

### Task B1: Point oauth_service at omniWA `web` client
**Objective:** Use the existing `web` OAuth client (redirect `https://api.narendar.tech/oauth/callback`) instead of Hermes' desktop/localhost.

**Files:** Modify `backend/app/services/oauth_service.py`
**Step 1:** Load client secret from `google_client_secret.json` (`web` type), read `client_id`, `client_secret`, `redirect_uris[0]`.
**Step 2:** `build_authorization_url(state)` builds URL with `redirect_uri=https://api.narendar.tech/oauth/callback` and scopes: gmail, calendar, drive, sheets, docs, contacts.
**Step 3:** Use `Authlib` or `requests` + `PKCE`. Store `code_verifier` server-side keyed by `state` (Redis).
**Step 4:** Test: `GET /oauth/authorize` returns 302 to `accounts.google.com/...&redirect_uri=https%3A%2F%2Fapi.narendar.tech%2Foauth%2Fcallback`. Commit.

### Task B2: Customer Google token storage (encrypted, per-customer)
**Objective:** Each customer's Google token stored separately + auto-refresh.

**Files:** Modify `backend/app/services/oauth_service.py` (`store_user_credentials`), `backend/app/api/oauth.py` (callback).
**Step 1 (test):** `tests/test_oauth_store.py`:
```python
def test_store_encrypts():
    creds = {"access_token":"at","refresh_token":"rt","expiry":1000}
    store_user_credentials(db, user, creds)
    row = db.query(CustomerGoogleToken).first()
    assert row.access_token_enc != "at"   # encrypted
    assert decrypt_token(row.access_token_enc) == "at"
```
**Step 2:** Encrypt tokens via `core/security.py` `encrypt_token` before DB write.
**Step 3:** Callback writes to `CustomerGoogleToken` keyed by `user_wa_phone`, NOT a single global file.
**Step 4:** Add `refresh_if_expired(phone)` using `refresh_token`.
**Step 5:** `pytest tests/test_oauth_store.py -v` → PASS. Commit.

### Task B3: "Connect Google" one-click UI
**Objective:** Customer sees one button → Google consent → Allow → done.

**Files:** Add template `backend/app/templates/connect_google.html`; route in `api/oauth.py`.
**Step 1:** Dashboard shows card: "Connect Google — Calendar / Gmail / Drive [Connect Google]".
**Step 2:** Button → `GET /oauth/authorize?state=<dashboard_session>`.
**Step 3:** Callback → `RedirectResponse("/dashboard?google_success=true")`.
**Step 4:** Verify in browser: click → Google consent (shows "omniWA wants…") → Allow → back on dashboard, status "Connected". Commit.

### Task C1: Permission cascade engine
**Objective:** Owner acts instantly; non-owner/group request held + routed to owner for approve/deny.

**Files:** Modify `backend/app/services/permission_service.py`; `backend/app/api/webhooks.py`.
**Step 1 (test):** `tests/test_permission.py`:
```python
def test_owner_auto_approve():
    assert decide(user=owner, sender="owner") == "run"
def test_stranger_held():
    d = decide(user=stranger, sender="stranger")
    assert d["action"] == "hold"
    assert d["needs_owner_approval"] is True
```
**Step 2:** Implement `decide(sender_phone, group, text) -> {"action": "run"|"hold"|"deny"}`:
- sender == owner → `run`
- sender in allowlist + `has_permission` → `run`
- else → `hold`, create `PendingDecision`, notify owner via WhatsApp/DM.
**Step 3:** Owner approves via dashboard or reply → enqueue task to Hermes.
**Step 4:** `pytest tests/test_permission.py -v` → PASS. Commit.

### Task C2: Wire webhook through permission layer
**Objective:** No message reaches Hermes without passing the cascade.

**Files:** Modify `backend/app/api/webhooks.py`.
**Step 1:** On inbound message: `decision = permission_service.decide(...)`.
**Step 2:** If `run` → `dispatch_to_hermes(phone, text)` (slim HTTP client to `:8642`).
**Step 3:** If `hold` → store `PendingDecision`, message owner "User X wants to: <task>. Approve?".
**Step 4:** Test: stranger texts → owner gets approval prompt; owner approves → Hermes runs. Commit.

## 5. Deployment: 2 containers + tunnel

**File:** Rewrite `docker/docker-compose.yml` (10 → 2 services + tunnel).

```yaml
services:
  backend:
    build: ./backend
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://redis:6379
      - HERMES_HTTP_URL=http://hermes:8642
      - GOOGLE_CLIENT_SECRET_PATH=/app/google_client_secret.json
      - BASE_URL=https://api.narendar.tech
    depends_on: [postgres, redis]
    ports: ["8000:8000"]

  postgres:
    image: postgres:16
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:
    image: redis:7

  hermes:
    image: nousresearch/hermes-agent:latest
    command: gateway run
    environment:
      - WHATSAPP_ENABLED=true
      - WHATSAPP_MODE=bot
      - HERMES_DASHBOARD=1
      - HERMES_HTTP_API=1
      - HERMES_API_KEY=${HERMES_API_KEY}        # backend sends this in :8642 calls
      - WHATSAPP_DM_POLICY=allowlist            # config-level gate (verified in source)
      - WHATSAPP_ALLOWED_USERS=${OWNER_WA_ID}   # only approved senders reach AI
      - WHATSAPP_GROUP_POLICY=allowlist
      - WHATSAPP_REQUIRE_MENTION=true           # @jarvis only
      - FALLBACK_PROVIDERS=gemini,nvidia,custom
    volumes: ["hermes_data:/opt/data"]
    ports: ["8642:8642", "9119:9119"]

**Multi-tenant isolation (Option Z — profiles):** One Hermes process runs **N profiles**, one per tenant (`~/.hermes/profiles/<tenant_slug>/`). Each profile owns its OWN memory + skills + **Google token slot**. omniWA provisions a profile per tenant at onboarding and passes `profile=<tenant_slug>` on every `:8642` call. **omniWA remains the credential owner**: Google tokens are stored encrypted in `customer_google_tokens` (tenant-scoped) and injected into the tenant's Hermes profile context per request — Hermes NEVER holds a shared global token (this prevents the cross-tenant Workspace leak caught in the grill).

**Container count correction:** original stack is **8 default services** (postgres, redis, litellm, hermes, mcp-server, openwa, backend, tunnel) + 2 optional audio (whisper, kokoro). Target: **backend + hermes (+ postgres + redis + tunnel as supporting)**. Hermes serves N tenant profiles within one container. "10→2" in the headline over-counts; accurate framing is **8 default → 3-4** (backend, hermes, postgres, redis; tunnel is routing).

Note: WhatsApp bridge runs inside the `hermes` container (verified working locally; in Docker it uses the same Baileys session at `/opt/data/whatsapp/session`).

  tunnel:
    image: cloudflare/cloudflared
    command: tunnel --url http://backend:8000
    # exposes https://api.narendar.tech for dashboard + OAuth callback
```

Note: WhatsApp bridge runs inside the `hermes` container (verified working locally; in Docker it uses the same Baileys session at `/opt/data/whatsapp/session`).

## 6. Validation (end-to-end)

1. `docker compose up` → only `backend`, `postgres`, `redis`, `hermes`, `tunnel` healthy.
2. Owner opens `https://api.narendar.tech/login` → logs in with DashboardUser creds.
3. Owner clicks "Connect Google" → consent → "Connected" (token in `customer_google_tokens`, encrypted).
4. Stranger texts bot in group `@jarvis do X` → owner gets "User X wants: do X. Approve?" → owner approves → Hermes executes via `:8642`.
5. Owner texts bot directly → runs instantly (no prompt).
6. `pytest tests/` all green.

## 7. Risks & Tradeoffs

- **Transport choice:** If omniWA keeps its own WhatsApp transport (full interception control), it does NOT use Hermes' bridge — instead routes Hermes as brain via `:8642`. If using Hermes' bridge, intercept via Hermes event hooks. **Decision needed before Task C2.**
- **Token isolation:** Per-customer encryption key should be app-level secret, not per-row; acceptable for v1.
- **Hermes HTTP API contract:** Exact `:8642` request/response shape must be confirmed from Hermes docs before Task C2 (payload format for chat dispatch).
- **Public URL dependency:** Dashboard + OAuth both need `api.narendar.tech` reachable; tunnel is mandatory.
- **Group mention detection:** `@jarvis` parsing stays in omniWA (Hermes does not natively map group sender→owner-approval).

## 8. Decisions (RESOLVED — verified against Hermes source)

- **Q1 (transport): USE HERMES BRIDGE.** Verified in `plugins/platforms/whatsapp/adapter.py` + `gateway/platforms/whatsapp_common.py:390-422`: Hermes already gates inbound messages via `dm_policy` / `group_policy` (`open|allowlist|disabled|pairing`), `allow_from`, `require_mention`, and `mention_patterns`. This is the **config-level gate** — free, no second transport. omniWA does NOT run its own WhatsApp connection. The dynamic owner-approve/stranger-held **cascade runs as a separate layer in omniWA's FastAPI backend**: Hermes' allowlist *rejects* unapproved senders (they hit the existing "🔒 Setup mode" path), and owner approval → `dispatch_to_hermes()` via `:8642`. No pre-AI code hook exists in Hermes, so the cascade lives in `permission_service.py`, not in the message path.
- **Q2 (tenancy): MULTI-TENANT.** Add `Tenant` model; each tenant = one business with its own dashboard login(s), its own WhatsApp number/session, and isolated `customer_google_tokens`. Dashboard is per-tenant. See Task A1 update.
- **Q7 (Google OAuth app): P1 (shared omniWA web client, per-tenant tokens).** Consent shows "omniWA"; per-tenant isolation is at the token-storage layer (`customer_google_tokens.tenant_id`), NOT a per-tenant Google project. P2 (white-label per-tenant Google app) deferred to enterprise tier.
- **Q8 (rate-limit + queue): R1 (omniWA keeps thin inbound layer).** Hermes bridge forwards inbound events to omniWA; omniWA applies Redis `rl:{sender}` 20/min + per-chat queue (depth 5) + permission cascade, then dispatches approved messages to `:8642`. Hermes' debounce batching is NOT a substitute for these guards — they are preserved in omniWA.
- **Q9:** (superseded by Q4 — Option Z profiles already decided isolation)
- **Q10 (ADR-0003 generic tools): T2 (pure native, NO MCP server for v1).** Hermes native tools cover web/calendar/drive/gmail per-profile. `database_query` primitive DROPPED (unnecessary — tenant data lives in Google; unsafe — AI free-SQL on auth tables). `http_request` added later only if a tenant needs custom REST integration. This partially reverses ADR-0002's "keep mcp_server" — MCP server is DROPPED entirely for v1.

<!--END-->




