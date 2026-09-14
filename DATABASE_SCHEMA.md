# omniWA — Database Schema (v2)

Regenerated from code — the single source of truth is
`backend/app/models/models.py` (Postgres) and `backend/app/intake/streams.py`
plus the `cache_get`/`cache_set` call sites (Redis). The freshness gate
`backend/tests/test_repo_contract.py` fails if a table is added/renamed
without updating this document.

## Schema Management

**Alembic owns the schema** (`backend/alembic/`). Apply with
`alembic upgrade head`. The SQL files in `backend/migrations/` are legacy
`initdb` scripts that run once on a *fresh volume*; new table/column changes
must be Alembic revisions.

## PostgreSQL (15 tables)

### `tenants`
Business customer. One tenant = one WhatsApp number + dashboard + token namespace.
`id` PK · `name` · `slug` unique (Hermes profile key) · `whatsapp_session_ref`
(Hermes session/profile ref) · `is_active` · `created_at`.

### `users`
WhatsApp user. `id` UUID PK · `tenant_id` FK → tenants (NULL = legacy
single-tenant rows) · `wa_phone` unique · `display_name` · `timezone`
(default Asia/Kolkata) · `is_owner` · `has_permission` (owner grants bot use) ·
`trust_level` (`trusted|untrusted`, sender trust gate) ·
`google_access_token_enc` / `google_refresh_token_enc` / `google_token_expiry`
· `created_at` / `updated_at`.

### `observed_contacts`
WhatsApp identities seen in DMs/allowed groups (Hermes contact-ingest pushes;
replaces the Evolution-era Redis contacts cache). `id` UUID PK · `tenant_id`
FK → tenants · `wa_phone` · `lid` · `display_name` · `source_chats` JSON ·
`first_seen_at` / `last_seen_at`. Unique (`tenant_id`, `wa_phone`).

### `dashboard_users`
Humans logging into the web dashboard (per-tenant, argon2 hash). `id` PK ·
`tenant_id` FK → tenants · `email` · `password_hash` · `is_owner` ·
`created_at`. Unique (`tenant_id`, `email`).

### `api_keys`
Provider keys surfaced to LiteLLM config rebuilds. `id` UUID PK · `name` ·
`provider` (groq, openrouter, gemini) · `api_key_enc` · `is_active` ·
`created_at` / `updated_at`.

### `customer_google_tokens`
Per-customer Google OAuth tokens, encrypted at rest, tenant-scoped (Hermes
never holds a shared global token). `id` PK · `tenant_id` FK → tenants ·
`user_wa_phone` · `owner_id` FK → users (SET NULL) · `access_token_enc` ·
`refresh_token_enc` · `token_expiry` · `scopes` · `email` · `created_at` /
`updated_at`. Unique (`tenant_id`, `user_wa_phone`).

### `events_cache`
Google Calendar event cache (conflict detection). `id` UUID PK · `user_id` FK
→ users · `google_event_id` · `summary` · `description` · `location` ·
`start_time` / `end_time` · `attendees` JSON · `meet_link` · `source_chat`
(group JID or sender phone) · `source_message_id` · `status` (default
confirmed) · `created_at` / `updated_at`. Unique (`user_id`,
`google_event_id`); index (`user_id`, `start_time`).

### `reminders`
Proactive reminders (Hermes cron owns scheduling; no Redis sorted set).
`id` UUID PK · `user_id` FK → users · `event_id` FK → events_cache (nullable)
· `reminder_type` (15min, 1hour, 1day, briefing, summary, conflict, weekly) ·
`scheduled_at` · `sent_at` · `status` (pending|sent|failed|cancelled) ·
`payload` JSON · `created_at`.

### `pending_decisions`
Permission requests awaiting owner DM confirmation. `id` UUID PK · `user_id`
FK → users · `short_code` unique (e.g. A1B2) · `action_type`
(create_event|delete_event|group_reply|update_event) · `proposed_action` JSON ·
`source_chat` · `status` (awaiting|approved|rejected|expired) · `expires_at` ·
`resolved_at` · `created_at`.

### `sessions`
Active chat sessions (DM phone or group JID). `id` UUID PK · `user_id` FK →
users · `chat_id` · `is_group` · `last_message_at` · `created_at`.

### `chat_acl`
Per-chat allowlist/policy. `chat_id` PK · `is_group` · `mode` (default
silent_log) · `memory_enabled` · `voice_enabled` · `display_name` · `notes` ·
`created_at` / `updated_at`.

### `sender_acl`
Per-sender trust. `sender_phone` PK · `trust_level` (default unknown) ·
`display_name` · `notes` · `created_at` / `updated_at`.

### `user_preferences`
Learned preferences. `id` UUID PK · `user_id` FK → users · `key` · `value` ·
`source` (default explicit) · `created_at` / `updated_at`. Unique (`user_id`,
`key`).

### `preference_proposals`
Confirmations before auto-promoting a preference. `id` UUID PK · `user_id` FK
→ users · `action_class` · `target_id` · `confirmation_count` ·
`proposed_at` · `accepted` · `declined_until` · `created_at`. Unique
(`user_id`, `action_class`, `target_id`).

### `audit_log`
`id` UUID PK · `user_id` FK → users · `action` · `details` JSON ·
`created_at` (indexed).

## Redis

### Intake stream (durable queue)
`omniwa:inbound` stream, consumer group `agent_workers`, single
`StreamConsumer` with PENDING reclaim (`XAUTOCLAIM`) and dead-letter stream
`omniwa:inbound:dead` after max attempts. Per-chat pending cap (default 5).

### Intake bookkeeping (hashes/keys with `inbox:` prefix)
- `inbox:pending` hash: `chat_id` → pending count (depth/complete).
- `inbox:idem:<key>` dedupe markers, TTL 24h.
- `inbox:rl:<sender>` fixed-window rate-limit counters.

### Dashboard sessions (strings, TTL)
- `dash_session:<sid>` → `<tenant_id>:<user_id>`, TTL 24h (cookie `omniwa_session`).
- `session:<id>` legacy fallback rows.

### OAuth + bot identity (strings via `cache_get`/`cache_set`, default TTL 3600s)
- `oauth_state:<state>`, `whatsapp:owner_phone`, `whatsapp:bot_phone`.

## v1 → v2 renames (do NOT reintroduce the left side)

| v1 (stale copies/docs) | v2 (this codebase) |
|---|---|
| `message_queue` stream | `omniwa:inbound` stream |
| `delayed_jobs` sorted set, `reminders.redis_job_id` | Hermes cron owns proactive; `reminders.status` |
| `decisionstate` enum, `pending_decisions.state/event_id` | `pending_decisions.status/short_code/proposed_action` |
| `sessions.session_data/expires_at` | `sessions.chat_id/is_group/last_message_at` |
| `users.is_active/last_auth_time` | `users.tenant_id/trust_level/has_permission` |
| Meta Cloud API webhook + `agent_worker`/`scheduler_worker` | Evolution-API bridge + intake `StreamConsumer` |

## Data Flow

1. Evolution bridge event → intake seam (`StreamsInbox` + gates + policy) → `omniwa:inbound`.
2. `StreamConsumer` → admission (`admit`: idempotency → sent-log echo guard → pending cap → enqueue).
3. Agent (Hermes via LiteLLM) acts; outbound seam sends; `pending_decisions` gate owner approval.
4. Google writes land in `events_cache`; proactive rows in `reminders`.

## Connection Details

Postgres `postgres:5432` / `localhost:5432`, db `calendar_agent`, user
`calendaruser` (compose defaults; CI service matches). Redis `redis:6379` /
`localhost:6379`, password from `.env`. Tests suffix the DB with `_test`
(`backend/tests/conftest.py`) and fall back to localhost when the
docker-network hostnames are unreachable.
