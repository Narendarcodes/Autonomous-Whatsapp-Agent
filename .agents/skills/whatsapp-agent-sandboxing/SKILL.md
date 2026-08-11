---
name: whatsapp-agent-sandboxing
description: Architecture, verification, and implementation patterns for WhatsApp agent sandboxing, dual-instance session isolation, rate limiting, and permission boundaries.
---

# WhatsApp Agent Sandboxing & Session Isolation Skill

This skill provides guidelines and patterns for implementing, verifying, and maintaining production-grade sandboxing for the WhatsApp AI Agent system based on production multi-agent design patterns (Nir Diamant - agents-towards-production).

## Core Principles

### 1. Dual-Instance Session Sandboxing
- **Primary Owner Session (`my-session`)**: Used for owner management, administrative commands (`/allow`, `/block`, `/quiet`), and single-number self-chat mode.
- **Secondary Agent Session (`agent-session`)**: Dedicated sandbox for the AI Agent identity. Webhook events coming from `agent-session` are isolated and routed exclusively through the agent instance.
- **Loop Prevention**: Outbound messages sent by the agent or primary API must cache message IDs in Redis (`sent_message:{id}`) with a 1-hour TTL. Inbound webhooks matching cached IDs must be silently dropped.

### 2. Multi-User & Multi-Chat Boundary Protection
- **Session Mapping**: `X-Hermes-Session-Id` header sent to Hermes Agent MUST equal the unique chat JID (`chat_id`: phone number for DMs, `1203...` / `...-...@g.us` for group chats). NEVER mix owner phone JIDs with non-owner or group chat session IDs.
- **ACL Sandboxing**:
  - `allow_all`: Unrestricted access for authorized DMs/groups.
  - `silent_log`: Logs incoming messages without executing actions or triggering agent responses unless whitelisted.
  - `block`: Immediately drops messages at the webhook level.
- **Trust Level Sandboxing**:
  - `owner`: Full privileges, bypasses quiet hours and ACL restrictions.
  - `trusted`: Allowed to trigger agent execution in authorized chats.
  - `untrusted`: Webhook drops messages automatically.

### 3. Rate Limiting & Queue Sandboxing
- **Sliding-Window Rate Limiter**: 20 requests/minute per sender phone tracked in Redis (`rl:{sender_phone}`). Exceeding senders receive a system warning alert and are rejected.
- **Per-Chat Async Queue**: Each `chat_id` has an independent `asyncio.Queue` (max length 5) processed sequentially by a dedicated worker task to prevent race conditions and out-of-order execution.

## Verification Checklist
- [ ] Inbound webhooks from `agent-session` only process authorized target chats.
- [ ] `bot_mode == "dual_number"` ignores primary session messages to prevent double processing.
- [ ] Redis idempotency key (`{instance}:{message_id}`) prevents duplicate webhook execution within 24h.
- [ ] Owner approval flow for `pending_decisions` routes approval confirmation back to `source_chat`.
