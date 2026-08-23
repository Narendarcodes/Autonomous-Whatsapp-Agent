# omniWA

> A self-hosted, WhatsApp-native AI assistant built on the Hermes Agent — with a multi-tenant web dashboard, Google Workspace tools, and allowlist-gated access control.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker_Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![License](https://img.shields.io/github/license/Narendarcodes/Autonomous-Whatsapp-Agent?style=for-the-badge)

---

## What is omniWA?

omniWA turns a WhatsApp account into an AI assistant you can talk to like a colleague: it remembers conversations per chat, manages your Google Calendar / Drive / Docs / Sheets / Gmail through natural language, runs scheduled jobs, and stays under your control through a web dashboard.

It is **not** a chatbot behind a webhook. WhatsApp transport is owned by a [Hermes Agent](https://github.com/NousResearch/Hermes-Agent) native Baileys bridge running inside the same container as the agent brain. A FastAPI service acts purely as the **control plane**: dashboard auth, pairing, connection-mode switching, Google OAuth, permissions, and health — never in the message path.

### Highlights

- **Two connection modes** — pair the agent to your own number (*self chat*) or a dedicated *bot number*, and switch live from the dashboard (~1 min restart, pairing preserved)
- **Allowlist access control** — permit individual numbers, JIDs and group JIDs; DM/group policies (`allowlist` / `open` / `disabled`) and a group @mention requirement are editable in the UI
- **Group Privacy Mode** — Hermes plugin hooks inject a privacy directive into group turns and scrub emails, phone numbers and long numeric tokens out of group replies before delivery
- **Per-chat persistent memory** — one Hermes session per chat target, surviving restarts
- **Google Workspace tools** — Calendar, Drive, Docs, Sheets, Gmail via PKCE OAuth; refresh tokens encrypted per tenant in PostgreSQL
- **Multi-tenant dashboard** — email + password accounts (argon2id), Redis-backed sessions with instant revocation
- **Native scheduling** — proactive jobs run on Hermes cron, not a bolted-on scheduler
- **Production posture** — 5-service Docker Compose stack, health checks end-to-end (app / postgres / redis / bridge / hermes), ~150-test pytest suite

---

## Architecture

```
WhatsApp ⇄ Baileys bridge (inside hermes container, :8642)
              │   allowlist · DM/group policy · @mention gate
              ▼
        Hermes gateway ──── one session per chat target
              │
        Hermes Agent (ReAct loop, memory, cron)
        ├── tools: Google Calendar · Drive · Docs · Sheets · Gmail
        └── reply delivered by the bridge itself

FastAPI backend (:8000) ── control plane, off the message path
  ├── dashboard UI + argon2 login + Redis sessions
  ├── WhatsApp pairing (QR) & disconnect
  ├── bridge configuration (mode/policies → shared volume + restart)
  ├── Google OAuth connect (per-tenant encrypted tokens)
  ├── permissions/trust management · preferences · API keys · health
  └── owner notifications via bridge POST /send
```

### Container stack

| Service | Port | Role |
| :--- | :--- | :--- |
| **hermes** | 8642 | Nous Research Hermes Agent: gateway, OpenAI-compatible API, native Baileys bridge |
| **backend** | 8000 | FastAPI control plane: dashboard, pairing, OAuth, permissions, health |
| **postgres** | 5432 | Users, tenants, ACLs, encrypted Google tokens, audit logs |
| **redis** | 6379 | Dashboard sessions, caches |
| **tunnel** | — | Optional Cloudflare tunnel profile for a public URL |

The backend reads/writes the shared `hermes_data` volume (pairing session, runtime bridge config, soul/plugins) and can restart the hermes container over the Docker socket — that's how mode/policy changes apply without touching a terminal.

---

## Quick Start

### Prerequisites

- Docker + Docker Compose
- A WhatsApp account to pair (QR scan)
- Google Cloud OAuth client (only if you want Workspace tools)

### 1. Configure

```bash
cp backend/.env.example backend/.env
# fill in at least:
#   ADMIN_PASSWORD, SESSION_SECRET_KEY, OWNER_WA_PHONE,
#   TOKEN_ENCRYPTION_KEY, GOOGLE_CLIENT_ID / _SECRET
```

For a public URL via Cloudflare Tunnel, put `TUNNEL_TOKEN=...` in `docker/.env`.

### 2. Launch

```bash
cd docker
docker compose up -d            # core: postgres, redis, hermes, backend
docker compose --profile public up -d   # add the tunnel when ready
```

### 3. Create the owner account

```bash
cd backend
python -m scripts.seed_owner --email you@example.com --password 'a-strong-password'
```

(Inside the deployment instead: `docker exec -it <backend-container> python -m scripts.seed_owner ...`.)

### 4. Pair and go

1. Open `http://localhost:8000/login` (or your domain) and sign in.
2. **WhatsApp tab** → scan the QR with the phone you're pairing.
3. **Identity tab** → pick *Self Chat* or *Bot Number*; set DM/group policies and the allowlist; hit **Apply & Restart Agent**.
4. **Permissions tab** → grant contacts; **Connect Google** → finish OAuth.

Message the paired number and you're talking to your agent.

---

## Configuration

Key variables in `backend/.env` (see `.env.example` for the full annotated list):

| Variable | Purpose |
| :--- | :--- |
| `ADMIN_PASSWORD` | Legacy bootstrap admin password |
| `SESSION_SECRET_KEY` | Dashboard session signing key — change it |
| `OWNER_WA_PHONE` | Owner identity used by ACL/OAuth flows |
| `TOKEN_ENCRYPTION_KEY` | Fernet key encrypting Google tokens at rest |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Workspace OAuth client |
| `POSTGRES_*`, `REDIS_*` | Data stores |
| `HERMES_BASE_URL`, `HERMES_API_KEY` | Backend ↔ Hermes API |
| `WHATSAPP_MODE`, `WHATSAPP_DM_POLICY`, `WHATSAPP_GROUP_POLICY`, `WHATSAPP_REQUIRE_MENTION`, `WHATSAPP_ALLOWED_USERS` | Bootstrap bridge config (runtime changes live on the shared volume via the dashboard) |

Runtime WhatsApp configuration lives on the `hermes_data` volume (`bridge_env` + policy block in `config.yaml`) and is managed from the dashboard — the env vars above only seed the first boot.

---

## Testing

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -q     # Windows venv layout
```

Covers endpoints/auth, pairing + bridge config round-trips, permission cascade logic, group-privacy wiring, phone utilities and more (~150 tests against real Postgres + Redis test instances).

---

## Security & Privacy

- **Access is deny-by-default**: with `dm_policy=allowlist` only explicitly permitted numbers/JIDs reach the agent.
- **Group Privacy Mode**: two Hermes-side layers — a system-prompt directive for group turns plus deterministic redaction of outbound group replies; DMs untouched. Group streaming and tool-progress bubbles are disabled so partial output never leaks.
- **Encrypted secrets**: Google refresh tokens are Fernet-encrypted per tenant row; dashboard sessions are HttpOnly cookies backed by instantly-revocable Redis keys.
- **No message transit through the backend**: the control plane never sees chat content; it cannot read your conversations.

### Known limitations

Tracked in [CONTEXT.md](CONTEXT.md) ("Known Gaps & Future Work"). Notably: inbound per-sender rate limiting is not currently enforced (safe under a tight allowlist; revisit before opening DMs).

---

## Documentation

- [CONTEXT.md](CONTEXT.md) — current architecture, key files, runtime configuration, known gaps
- [docs/adr/](docs/adr/) — architecture decision records (see `0007` for the v3 transport redesign)
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — PostgreSQL schema
- [AGENTS.md](AGENTS.md) — repo conventions for coding agents
- [hermes-plugin/](hermes-plugin/) — the agent's soul prompt + group-privacy plugin source

---

## License

[MIT](LICENSE)
