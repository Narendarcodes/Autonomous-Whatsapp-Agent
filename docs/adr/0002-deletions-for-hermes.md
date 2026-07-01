# 2. Deletions for Hermes Migration (Option B)

Date: 2026-06-13

## Status

Accepted

## Context

By migrating to Hermes Agent as our core reasoning and memory engine (but keeping our FastAPI middleman for permissions and Evolution API for WhatsApp), a significant portion of our existing custom features are now redundant. 

## Burn List (Code to Delete)

1. **Custom Schedulers & Workers**
   - `backend/app/workers/scheduler_worker.py`
   - `backend/app/services/proactive_service.py` / `proactive_scheduler.py`
   - `backend/app/infrastructure/delayed_scheduler.py` (if applicable)
   - *Reason:* Hermes handles cron, reminders, and delays natively.

2. **Custom Memory & Preferences**
   - `backend/app/services/preferences_service.py`
   - Redis conversation history logic (the 50 messages / 24-hour rolling window).
   - `user_preferences` table or similar bespoke memory schemas.
   - *Reason:* Hermes uses persistent "Skill Documents" and context windows natively via `X-Hermes-Session-Id`.

3. **Complex LLM Looping**
   - `backend/app/services/agent_engine.py` (Old implementation heavily reduced or deleted)
   - Any manual tool-chaining, function-calling parsers, or retry logic for the LLM.
   - *Reason:* Hermes has an autonomous ReAct loop. We just expose the tools via MCP (`backend/app/mcp_server/`), and Hermes handles the reasoning and execution.

4. **Proactive Conflict Scanners**
   - `backend/app/services/conflict_detection.py` (Custom background scanner)
   - *Reason:* We can schedule Hermes to proactively do a calendar check ("Check for conflicts today"), and Hermes will use MCP to run the query and resolve it natively.

## What Stays (The Shield)
- `permission_service.py` & `security_service.py` (Our permission gate)
- `whatsapp_service.py` (Evolution API sender)
- `mcp_server/main.py` (The tool host)
- `api/webhooks.py` (Receiving WhatsApp texts)
- Database schema for `pending_decisions` & `users` (OAuth tokens).
