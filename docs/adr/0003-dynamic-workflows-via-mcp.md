# 3. Dynamic Workflow Execution via MCP

Date: 2026-06-13

## Status

Accepted

## Context

The user envisions the WhatsApp agent acting as a "conversational operating system" handling an endless array of possibilities (expenses, emails, document organization, etc.). Writing custom hard-coded integration logic for every single new software category is not scalable or maintainable. 

## Decision

We will utilize Hermes' ability to dynamically use MCP (Model Context Protocol). Instead of hardcoding "expense saving" logic, we build generic, generalized MCP tools:
1. `http_request` (Allows Hermes to interact with arbitrary REST APIs)
2. `read_email` / `send_email` (Generic email primitives)
3. `append_to_file` / `read_file` (Generic Drive/document primitives)
4. `database_query` (Generic data insertion)

Hermes will rely on its internal instructions and ReAct loop to combine these generic primitives to solve bespoke user requests on the fly, without code changes on our end.

## Consequences

- The development effort is shifted from building bespoke feature logic (e.g., `create_family_timeline()`) to building atomic, generic capabilities.
- Hermes gains enormous horizontal capabilities instantly.
- The `permission_service.py` gate becomes incredibly important, as the tools are now highly generic and powerful. We must prompt the user before any generic `http_request` is sent.
