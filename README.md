# 🤖 WhatsApp AI Agent (Hermes Operating System v2.1)

> Enterprise-grade, privacy-compliant WhatsApp AI Assistant powered by the **Hermes ReAct Engine**, **LiteLLM Multi-Provider Fallback Router**, **FastMCP Google Workspace Integration**, and **Evolution API**.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-27+-blue.svg)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-red.svg)](https://redis.io/)
[![Hermes](https://img.shields.io/badge/Hermes-Agent-orange.svg)](https://github.com/NousResearch/hermes-agent)
[![LiteLLM](https://img.shields.io/badge/LiteLLM-Router-purple.svg)](https://github.com/BerriAI/litellm)
[![Evolution API](https://img.shields.io/badge/Evolution-API-2785-teal.svg)](https://github.com/EvolutionAPI/evolution-api)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Overview

The **WhatsApp AI Agent** turns WhatsApp into a full-fledged **Conversational Operating System**. Built around Nous Research's **Hermes Agent ReAct Loop**, it operates autonomously, executes tool calls across your entire **Google Workspace** (Calendar, Drive, Docs, Sheets, Gmail), manages permissions for multiple users via a web dashboard, processes voice notes, enforces DPDP privacy compliance, and operates within an 8-container production Docker environment.

---

## ✨ Key Features

### 🤖 Intelligent Brain & ReAct Orchestration (Hermes Agent)
- **Autonomous Function Calling**: Executes complex multi-step tasks across registered MCP tools.
- **Persistent User Memory**: Session tracking mapped per user phone number across restarts.
- **Multi-Model Fallback Chain**: High-availability routing powered by **LiteLLM**:
  `GitHub Models (GPT-4o-mini) → Gemini 2.5 → Groq (Llama 3) → OpenRouter → NVIDIA NIM`.
- **Background Cron Execution**: Native scheduling for daily summaries, briefings, and reminders.

### 🔐 Permission & Access Control (ACL System)
- **Role-Based Access**:
  - 👑 **Owner (`is_owner=true`)**: Full admin rights, manages permissions, links Google OAuth, configures quiet hours.
  - 👥 **Authorized Users (`has_permission=true`)**: Can chat in DMs and trigger group mentions.
  - ⏳ **Pending Users (`has_permission=false`)**: Messages dropped silently at webhook layer with audit logging.
- **Web UI Admin Dashboard (`/dashboard`)**:
  - Live session authentication (`naru_session` cookie backed by Redis).
  - One-click contact whitelist / block toggling.
  - 1+ character autocomplete search with 300ms client debouncing & caching.
  - Quiet hours configuration & dynamic status alerts.
  - Real-time host machine hardware metrics (CPU, RAM, Disk).

### 🌐 Google Workspace Integration (FastMCP Bridge)
- 📅 **Google Calendar**: Create, search, update, delete events, check schedule conflicts, find free time slots.
- 📁 **Google Drive**: List, search, upload, and inspect workspace files.
- 📄 **Google Docs**: Read document contents, append text, generate new docs.
- 📊 **Google Sheets**: Query spreadsheets, insert rows, manage structured tables.
- ✉️ **Gmail**: Search unread messages, compose, send, and draft emails.
- 🌐 **Universal REST HTTP Tool**: Hermes can craft custom REST payloads to interact with external APIs.

### 📱 Advanced WhatsApp Messaging & DPDP Compliance
- **Evolution API (Baileys Wrapper)**: Webhook-driven WhatsApp integration with HMAC-SHA256 signature verification.
- **Idempotency & Rate Limiting**: Redis hash idempotency (24h TTL) + sliding window rate limiter (20 req/min).
- **Per-Chat Sequential Queue**: Asynchronous per-chat queues buffer incoming spikes without drops.
- **Quoted Reply Context**: Extracted directly from WhatsApp reply bubbles (`contextInfo.quotedMessage`) and fed into LLM prompts.
- **DPDP Compliance**: Group messages strictly filtered unless `@agent` is explicitly tagged; DMs routed seamlessly.
- **Voice Message Processing**: Base64 audio decoding + instant transcription via Groq / Whisper.

---

## 🏗️ Architecture

```
 WhatsApp User → Evolution API (2785) 
                    │
              Webhook Receiver (8000)
                    │
          [Signature Check + Idempotency Filter]
                    │
          [Sliding-Window Rate Limiting (Redis)]
                    │
          [Per-Chat Sequential Queue (asyncio)]
                    │
          [Voice Transcription + DPDP Compliance]
                    │
          [ACL + Quiet Hours Evaluation]
                    │
          [Google Setup Flow & Command Parser]
                    │
          [WhatsApp Quoted Reply Context Parser]
                    │
          [Sequential Dispatch to Hermes Brain]
                    │
    Hermes Agent (8642) + MCP Server (9000)
                    │
       [Tools: Calendar, Drive, Docs, Sheets, Gmail, HTTP]
                    │
          LiteLLM Router (4000)
                    │
     [Fallback Chain: GitHub → Gemini → Groq → OpenRouter → NIM]
```

### 🐳 Container Infrastructure (`docker-compose.yml`)

| Service | Port | Description |
| :--- | :--- | :--- |
| **backend** | `8000` | FastAPI app, webhook handler, ACL router, admin dashboard UI & OAuth endpoints. |
| **hermes** | `8642` | Nous Research Hermes ReAct agent engine with persistent state memory. |
| **mcp-server** | `9000` | FastMCP server exposing Google Workspace APIs, HTTP tools, and WhatsApp reply dispatcher. |
| **litellm** | `4000` | Multi-LLM provider router with automatic failover chain. |
| **openwa** | `2785` | Evolution API (Baileys protocol engine) maintaining WhatsApp web socket session. |
| **postgres** | `5432` | PostgreSQL 16 database storing users, ACL records, OAuth tokens, and audit logs. |
| **redis** | `6379` | Redis 7 caching layer for session storage, idempotency, rate limiting, and QR status. |
| **tunnel** | N/A | Cloudflare Tunnel serving `https://api.narendar.tech` to public webhooks securely. |

---

## 📁 Project Structure

```
Autonomous-Whatsapp-Agent/
├── backend/
│   ├── app/
│   │   ├── api/                  # FastAPI Endpoints
│   │   │   ├── webhooks.py       # WhatsApp webhook orchestrator & queue workers
│   │   │   ├── setup.py          # Dashboard setup, system stats & onboarding
│   │   │   ├── permissions.py    # Contact ACL management & sync
│   │   │   ├── oauth.py          # Google OAuth authentication flow
│   │   │   ├── health.py         # System liveness & readiness probes
│   │   │   └── logs.py           # Real-time WebSocket log streamer
│   │   ├── core/                 # Config, database, security & logger
│   │   ├── db/                   # SQLAlchemy async session & Redis client
│   │   ├── mcp_server/           # FastMCP tool definitions (Google Workspace, HTTP, WhatsApp)
│   │   ├── models/               # Database models (User, ACL, OAuth, AuditLog)
│   │   ├── schemas/              # Pydantic validation schemas
│   │   ├── services/             # Core business logic (WhatsApp, Agent, OAuth, Permissions)
│   │   └── templates/            # Glassmorphic Admin Dashboard & QR UI
│   ├── tests/                    # Automated Pytest suite
│   ├── Dockerfile                # Python 3.11 Backend image
│   └── requirements.txt          # Python dependencies
├── docker/
│   └── docker-compose.yml        # 8-service Docker orchestration
├── docs/                         # Extended documentation & architecture diagrams
├── scripts/                      # Testing & utility scripts
├── AGENTS.md                     # Agent skill instructions & customization
├── CONTEXT.md                    # Live architectural state & system parameters
├── DATABASE_SCHEMA.md            # Detailed PostgreSQL schema documentation
├── EVENT_DRIVEN_ARCHITECTURE.md  # Webhook queue & event specifications
└── README.md                     # Project documentation
```

---

## 🚀 Quick Start

### Prerequisites
- **Docker & Docker Compose** (v27+)
- **WhatsApp Account** (for initial QR pairing)
- **Google Cloud Project** (OAuth Client ID & Secret for Workspace tools)
- **GitHub PAT or LLM Provider API Keys** (GitHub Models, Gemini, Groq, OpenRouter)

### 1. Environment Setup

Copy `.env.example` to `backend/.env` and populate your credentials:

```bash
cp backend/.env.example backend/.env
```

Key environment variables in `backend/.env`:

```env
# Server & Security
ENVIRONMENT=production
ADMIN_PASSWORD=your_secure_admin_password
OWNER_WA_PHONE=919876543210

# Database & Redis
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_db_password
POSTGRES_DB=whatsapp_agent
REDIS_HOST=redis
REDIS_PORT=6379

# Evolution API (WhatsApp)
EVOLUTION_API_URL=http://openwa:2785
EVOLUTION_API_KEY=your_evolution_api_key
EVOLUTION_INSTANCE=AgentInstance

# Google Workspace OAuth
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=https://api.narendar.tech/oauth/callback

# LLM Providers (LiteLLM Router)
GITHUB_TOKEN=ghp_your_github_token
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
```

### 2. Start the Stack

Launch all 8 services using Docker Compose:

```bash
cd docker
docker-compose up -d
```

Check the status of running containers:

```bash
docker-compose ps
```

### 3. QR Code Pairing & Onboarding

1. Open the Setup Page in your browser:
   `https://api.narendar.tech/setup` (or `http://localhost:8000/setup`)
2. Log in using your `ADMIN_PASSWORD`.
3. Scan the generated WhatsApp QR Code with your phone to link Evolution API.
4. Click **Connect Google Workspace** to complete the OAuth 2.0 flow.

### 4. Admin Dashboard Access

Manage user permissions, monitor system load, and configure settings at:
`https://api.narendar.tech/dashboard` (or `http://localhost:8000/dashboard`)

- **Whitelist Contacts**: Grant permissions to specific contacts.
- **Quiet Hours**: Define hours during which the agent refrains from automated messaging.
- **System Metrics**: Inspect real-time CPU, Memory, and Disk usage.

---

## 🧪 Testing & Verification

Run the full integration test suite against the live Docker stack:

```bash
docker compose -f docker/docker-compose.yml exec -T backend python -m pytest -x --tb=short
```

### Verification Highlights:
- ✅ Authentication & session management (`naru_session` cookie verification).
- ✅ HMAC-SHA256 webhook signature check (`X-Evolution-Signature`).
- ✅ Redis sliding window rate limiting & idempotency hash verification.
- ✅ Audio voice message transcription pipeline.
- ✅ Quoted reply message parsing.
- ✅ Contact lookup debouncing and ACL authorization checks.

---

## 🔒 Security & Privacy

- **DPDP Privacy Compliance**: Strictly filters non-mentioned messages in group chats.
- **HMAC Signature Verification**: Validates all inbound Evolution API webhooks using `X-Evolution-Signature`.
- **Encrypted Token Storage**: AES-256 encrypted refresh tokens stored in PostgreSQL.
- **Session Authentication**: Secure `HttpOnly` session cookies backed by Redis for web endpoints.

---

## 📄 Documentation Index

- [CONTEXT.md](CONTEXT.md) — Architectural overview & active system status.
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — Full database schema & migrations guide.
- [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md) — Message processing pipeline details.
- [PRODUCTION_READY_FINAL.md](PRODUCTION_READY_FINAL.md) — Option B Hermes migration overview.
- [LIMITATIONS_AND_IMPROVEMENTS.md](LIMITATIONS_AND_IMPROVEMENTS.md) — System scaling roadmap.

---

## 📝 License

Distributed under the MIT License. See `LICENSE` for more information.
