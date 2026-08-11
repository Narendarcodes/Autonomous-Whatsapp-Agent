---
name: group-chat-agent-handling
description: Best practices, architecture, and verification guidelines for handling WhatsApp group chats, DPDP privacy compliance, member attribution, group ACLs, and permission approvals.
---

# WhatsApp Group Chat Agent Handling Skill

This skill defines the complete operational specification and verification procedures for managing WhatsApp group chats in omniWA (Nir Diamant - agents-towards-production).

## Architecture & Data Flow in Group Chats

```
Group Chat Member sends message in Group (JID: 120363xxx@g.us)
                     ↓
          Evolution API Webhook
                     ↓
  [DPDP Privacy Check: Explicit Mention (@agent / Bot Name)]
                     ↓ (If NO mention → SILENTLY DROP)
                     ↓ (If mention present → CONTINUE)
  [Extract Participant JID (sender) & Remote JID (group)]
                     ↓
  [Group Monitored Check (ChatACL for group JID)]
                     ↓
  [Sender Trust Check (User/SenderACL for participant)]
                     ↓
  [Prefix Sender Attribution: "[Name (+Phone)]: message"]
                     ↓
  [Dispatch to Hermes with Session ID = Group JID]
                     ↓
  [Hermes reply sent back to Group JID]
```

## Critical Rules for Group Chats

### 1. Privacy Compliance (DPDP Act Standard)
- In group chats, the agent MUST NOT read, index, or respond to ambient group conversation.
- A message is ONLY processed if it contains an explicit mention: `@agent` or the configured `bot_name` (e.g. `Jarvis`).
- All non-mention messages are dropped immediately in the webhook worker loop before database queries or LLM calls.

### 2. Sender vs. Group JID Handling
- `chat_id`: MUST be set to `remote_jid` (`1203...` or `...-...@g.us`). This routes the agent's outgoing reply to the group, not a DM to the sender.
- `sender_phone`: Extracted from `data.participant` (or `remote_jid` if DM).
- `push_name`: Extracted from `data.pushName`.
- **Attribution Prefixing**: Format text as `[Sender Name (+Phone)]: message` before forwarding to Hermes so the LLM understands who within the group said what.

### 3. Permission & Approval Workflow in Groups
- When a group member requests a high-risk action (e.g., creating a calendar event, deleting an entry), the backend creates a `PendingDecision` with a unique short code (e.g. `A1B2`).
- The system sends a notification to the **Owner's WhatsApp DM** asking for approval.
- Once the owner approves via DM (e.g. `/approve A1B2`), the action executes and a confirmation message is posted back to `source_chat` (the group chat).

### 4. Group ACL Modes
- `allow_all`: Allows whitelisted group members to interact with the agent.
- `silent_log`: Logs group interactions without taking action or generating replies.
- `block`: Ignore all messages from the group completely.

## Verification Checklist
- [ ] Group messages without `@agent` or `BOT_NAME` are dropped silently.
- [ ] Group replies are posted to the group JID, not the participant's individual DM.
- [ ] Prompt sent to Hermes includes participant name and phone number prefix.
- [ ] Owner approval notifications correctly identify the originating group chat.
