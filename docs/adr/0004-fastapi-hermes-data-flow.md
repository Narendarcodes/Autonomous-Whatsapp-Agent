# 4. Core Data Flow (The FastAPI Harness)

Date: 2026-06-13

## Status

Accepted

## Context

We need strict agreement on how our remaining FastAPI layer acts as a harness around Hermes Agent to ensure DPDP compliance and permission enforcement, now that the custom agent loops and schedulers have been removed.

## Decision

The lifecycle of an inbound message will strictly follow this 5-step loop:

1. **Receive:** Evolution API pushes an inbound message event to the FastAPI webhook endpoint.
2. **Filter (Privacy Gate):** `api/webhooks.py` evaluates the message. Group messages not explicitly invoking the agent (e.g., lacking `@Agent`) are silently dropped.
3. **Dispatch:** The message payload is sent via HTTP POST to the local Hermes API (`/v1/chat/completions`), using the user's phone number as the `X-Hermes-Session-Id` to maintain persistent memory.
4. **Hermes Runs (Tool Phase):** Hermes enters its autonomous ReAct loop. If it decides it needs to execute a tool (e.g., `execute_http_request`), it calls back into our FastAPI MCP server. FastAPI intercepts this, checks `permission_service.py`, registers a record in `pending_decisions` if required, and returns a synthetic "Paused" status to Hermes rather than executing the action.
5. **Hermes Replies:** Hermes parses the tool output, formats a human-friendly response (e.g., "Please explicitly reply YES to approve this transaction"), and invokes the `send_whatsapp_message` MCP tool to deliver the message via Evolution API.

## Consequences

- The webhook router becomes extremely thin—it essentially just filters for privacy and forwards to Hermes.
- All "business logic" regarding *how* something is accomplished is pushed to Hermes.
- Our security entirely relies on our MCP tool definitions returning a "Paused/Requires Approval" string instead of a hard error when triggered.