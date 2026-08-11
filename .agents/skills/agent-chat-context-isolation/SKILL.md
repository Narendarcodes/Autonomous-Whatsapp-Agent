---
name: agent-chat-context-isolation
description: Guidelines and implementation patterns for preventing agent chat confusion, managing multi-chat context, handling quoted replies, and isolating session memory.
---

# Agent Chat Context Isolation & Anti-Confusion Skill

This skill provides patterns for isolating context across WhatsApp chats, preventing conversation bleed between different users/groups, and maintaining clear memory boundaries for the AI agent (Nir Diamant - agents-towards-production).

## Common Causes of Agent Confusion & Fixes

### 1. Cross-Chat Conversation Bleed
- **Root Cause**: Reusing a static or single session ID when dispatching requests to the LLM agent / Hermes brain.
- **Fix**: Always bind `X-Hermes-Session-Id` strictly to `parsed["chat_id"]`.
  - For DMs: `chat_id` = `919999999999` (sender's normalized phone JID).
  - For Groups: `chat_id` = `120363012345678@g.us` (group JID).
- **Result**: Hermes maintains separate persistent conversation buffers for every chat independently.

### 2. Quoted Reply Context Misattribution
- **Root Cause**: The agent loses context when users reply to a specific previous message bubble.
- **Fix**: Evolution API webhook extracts `contextInfo.quotedMessage`. Format quoted reply text before dispatching:
  ```python
  if quoted_text:
      final_text = f'[Replying to: "{quoted_text}"] {message_text}'
  ```

### 3. Identity & Environment Injection
- **Root Cause**: Agent doesn't know who it is talking to, what local time it is, or which tools are connected.
- **Fix**: The system prompt injected into Hermes must include dynamic metadata:
  ```python
  omniwa_os_context = (
      f"[SYSTEM IDENTITY & ENVIRONMENT CONTEXT]\n"
      f"Configured identity name: {bot_name}\n"
      f"Conversing with owner/user: {owner_name}\n"
      f"Current system date and time: {current_time_str}\n"
      f"Operating relationship mode: {bot_mode}\n"
      f"[ACTIVE SYSTEM CONNECTIONS]\n{active_connections_str}"
  )
  ```

### 4. Memory & Token Window Limits
- **Max Messages**: Keep `CONVERSATION_MAX_MESSAGES` at 50 with a 24-hour TTL (`CONVERSATION_TTL_SECONDS = 86400`).
- **Memory Toggle**: Respect `/memory-off {chat_id}` command by omitting session history or passing stateless instructions when disabled.

## Verification Checklist
- [ ] Direct messages from User A do not affect Hermes memory for User B.
- [ ] Group chat messages from Group X use Group X JID as session ID.
- [ ] Quoted message replies correctly prepend `[Replying to: "..."]`.
- [ ] Disconnected tools in system prompt guide users to dashboard rather than CLI setup.
