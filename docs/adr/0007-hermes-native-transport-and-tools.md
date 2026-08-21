# 7. Hermes-Native Transport & Tools (Multi-Tenant)

Date: 2026-08-21

## Status

Accepted — **supersedes ADR-0002** (Deletions for Hermes Migration) and **partially supersedes ADR-0003** (Dynamic Workflow Execution via MCP).

## Context

ADR-0002 kept Evolution API (`whatsapp_service.py`), the MCP server, and `preferences_service.py` as "The Shield," reasoning that Hermes only provided reasoning/memory while transport and tool-hosting stayed ours. Three verified facts reversed this:

1. **Hermes' native Baileys WhatsApp bridge works** (paired and verified locally). It also ships a config-level inbound gate: `dm_policy`/`group_policy` (`open|allowlist|disabled|pairing`), `allow_from`, `require_mention` — see `gateway/platforms/whatsapp_common.py`.
2. **Hermes has no pre-AI code hook** — the gate is boolean config, not an interception point. Our dynamic permission cascade therefore cannot live inside Hermes' message path.
3. **One shared Hermes is unsafe for multi-tenant credentials.** A single global Google token slot means tenant B's "create a doc" could execute against tenant A's Workspace (cross-tenant leak caught during architecture grilling). Hermes *does* support fully isolated profiles (memory + skills + tools per profile, `docs/profile-routing.md`).

Additionally, ADR-0003's `database_query` primitive was challenged and dropped: tenant business data lives in Google Workspace (covered by Hermes-native tools), so a generic SQL primitive would only expose omniWA's own auth tables to free-form LLM queries — unnecessary and unsafe.

## Decision

1. **Transport = Hermes bridge.** Evolution API / `openwa` container is dropped. omniWA keeps a **thin inbound layer**: Hermes bridge forwards events → omniWA applies Redis rate-limit (20/min) + per-chat queue (depth 5) + permission cascade → approved messages dispatch to Hermes `:8642`. Replies are delivered by Hermes itself (`HERMES_OWNS_WHATSAPP=true`).
2. **Isolation = Option Z (per-tenant Hermes profiles).** One Hermes process serves N tenants via isolated profiles; omniWA owns all Google tokens encrypted in Postgres (`customer_google_tokens`, tenant-scoped) and never writes a shared global token file.
3. **Tools = pure native (T2).** MCP server is dropped for v1. `database_query` is removed from the ADR-0003 primitive list. `http_request` may be added later behind an explicit tenant need.
4. **Auth = full authN/authZ.** Multi-tenant schema (`tenants`, `dashboard_users`, `customer_google_tokens`), argon2 password hashing, Redis-backed revocable sessions (no JWT), per-request tenant scoping.

## Consequences

- Container count: 11 services → 5 (postgres, redis, hermes, backend, tunnel).
- The permission cascade ("stranger asks → hold → owner approves") remains **omniWA's product moat**, implemented in `permission_service.decide()` — Hermes cannot do this natively.
- LiteLLM is replaced by Hermes' native provider fallback chain (supersedes ADR-0005's routing layer).
- Per-tenant white-label Google OAuth consent (each tenant's own Google Cloud project) is deferred to the enterprise tier; v1 uses one shared omniWA web client with per-tenant token isolation.
