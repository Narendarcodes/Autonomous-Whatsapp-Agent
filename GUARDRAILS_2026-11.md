# omniWA guardrails audit (2026-11-13)

This audit covers the WhatsApp gates, the one-number and two-number modes, dangerous commands, and prompt injection. The work was checked against Hermes 0.23.1 source and a live Docker stack.

## How a message is gated

| Layer | What it decides | Where |
|---|---|---|
| Baileys bridge (Hermes) | Which chats reach Hermes at all. Self-chat mode admits only the owner's "Message yourself" chat. Bot mode admits DMs from `allow_from` and groups listed in `group_allow_from`. | `whatsapp-bridge/bridge.js` |
| Hermes gateway | Mention gating in groups (`require_mention`) and slash-command access. | `gateway/platforms/whatsapp_common.py` |
| **omniwa-guard (new)** | Blocklist, flood limit, which group members are heard, injection flagging, which tools each sender may use, and secret scrubbing. | `hermes-plugin/omniwa-guard` |
| omniwa-group-privacy | Privacy directive in groups, plus redaction of emails, phone numbers and IDs from group replies. | `hermes-plugin/omniwa-group-privacy` |
| omniwa-assist | Keyword gate for group chatter (it now defers to the guard). | `hermes-plugin/omniwa-assist` |

### One number (self-chat) vs two numbers (bot)
- **Self-chat (one number):** the agent runs on the owner's own WhatsApp. The bridge drops everything except the owner's chat with themselves, including other people's DMs and every group. Allowlists, the blocklist and omniwa-assist have no effect in this mode, and every turn is the owner's.
- **Bot (two numbers):** the agent has its own number, and other people message it. This is where allowlists, groups and guests matter, and where every finding below applies.

## Findings: fixed in this PR

1. **Any member of an allowlisted group could use the owner's account.** Allowlisting a group JID admits every member. Hermes runs one profile, so each member acted with the owner's Google token, memory and terminal. *Fix:* the guard only hears the owner and allowlisted people inside groups. Setting `OMNIWA_GROUP_GUESTS=allow` lets other members chat, but only with guest tools.
2. **No tool limits for non-owners.** The `hermes-whatsapp` toolset includes `terminal`, `execute_code`, file read and write, `computer_use`, `memory`, `session_search` and `cronjob_manage`. *Fix:* non-owners now get only web search and extract, vision, image generation, TTS, clarify and todo. Even the owner can't use shell or file tools inside a group, because the output and any approval prompts there are visible to everyone.
3. **Secrets were readable through the terminal.** Hermes runs the `local` terminal backend inside the container that holds `/opt/data/.env` (every API key), the WhatsApp session `creds.json` and `google_token.json`, which is written in plaintext despite what SOUL.md claimed. *Fix:* any shell or file call that touches those files, dumps the environment, pipes a download into a shell, deletes `/` or `/opt/data`, or uploads local files is blocked for everyone. API keys, tokens, JWTs, private keys and `KEY=value` lines are scrubbed from tool results and from every WhatsApp reply.
4. **Revoke in the dashboard did nothing.** `/permissions/grant` and `/permissions/revoke` only changed a Postgres row, and nothing in the Hermes path reads it. *Fix:* grant adds the person to Hermes `allow_from` and lifts any block. Revoke removes them and adds them to the guard blocklist, which takes effect on the very next message with no restart.
5. **There was no blocklist.** *Fix:* the guard reads `/opt/data/omniwa_guard.json` (written by the backend) and the `OMNIWA_BLOCKED_SENDERS` variable. Phone numbers and LIDs are matched through Hermes' own alias map.
6. **No inbound rate limit.** *Fix:* each non-owner sender is limited to 8 messages per minute (`OMNIWA_RATE_LIMIT_PER_MIN`). The owner is exempt.
7. **Prompt injection.** *Fix:* non-owner messages that match known injection patterns (ignore previous instructions, reveal the system prompt, fake `</system>` tags, "I'm the owner, grant…", requests to read keys or forward all emails) are wrapped in an untrusted-input directive. Fetched content (web pages, email bodies, files) that contains instructions is flagged before the model reads it. SOUL.md now has a guardrails section.
8. **The owner's memory leaked to guests.** Hermes injects the profile's memory and user profile into every session. *Fix (partial):* non-owner turns get a guest-mode directive, and `memory` and `session_search` are blocked for them. A real fix needs a separate Hermes profile per person.
9. **Plugin order could cancel the guard.** Hermes uses the first skip or rewrite any plugin returns, so an assist keyword rewrite could beat a guard skip. Likewise only one `transform_llm_output` result is used, so group redaction and secret scrubbing could cancel each other. *Fix:* assist defers to the guard's verdict, and both output hooks now apply both rules.
10. **Ports were exposed to the internet.** Compose published Postgres, Redis, Hermes and the backend on `0.0.0.0`. *Fix:* they're bound to `127.0.0.1`, and the tunnel still reaches them over the Docker network.

## Still not handled (known gaps)

- **Pattern-based injection detection can be bypassed** by paraphrases, other languages, base64 or text inside images. The real control is the tool policy: even a successful injection from a guest can't reach owner-only tools.
- **Voice notes** are transcribed after `pre_gateway_dispatch`, so the injection wrapper doesn't see them. The tool policy still applies.
- **The owner's own injected content.** If the owner forwards a malicious message or opens a poisoned page, the owner's tools stay available. Tool results get flagged and destructive commands are blocked, but this risk isn't zero.
- **Hermes approvals:** `approvals.approvers` defaults to `*`. Approval prompts can now only arise in the owner's DM, because shell tools are blocked in groups and for guests. Setting `approvals.approvers` to the owner's ID would close this fully.
- **Bot-mode owner detection** uses `OWNER_WA_PHONE`. If WhatsApp sends only a LID that Hermes hasn't mapped yet, add it to `OMNIWA_OWNER_IDS`.
- **The rate limiter lives in memory** and resets when Hermes restarts.
- **Legacy dead code:** `security_service.py` (trust levels, blocked chats) and `command_parser.py` (`/block`, `/allow` commands) aren't wired into the Hermes path.
- **The Hermes image is unpinned** (`:latest`).
- **Multi-tenant:** one Hermes profile serves everyone. True isolation needs one profile per tenant.

## Verification

- Unit tests: guard 22, assist 17, group privacy 8, all inside the real Hermes 0.23.1 image. Backend: 198 tests.
- Live: the real Hermes plugin manager loaded all three plugins. `_dispatch_pre_tool_call_hooks` blocked `cat /opt/data/.env`, `creds.json` reads, `curl | sh`, terminal in groups, and terminal, memory and session_search for guests. It allowed `ls` for the owner and `web_search` for guests. API keys were scrubbed from replies.
- Live dashboard test: grant wrote `allow_from`, and revoke removed the person and blocklisted them.
- Not tested: real WhatsApp traffic, which needs a paired phone and an LLM key.
