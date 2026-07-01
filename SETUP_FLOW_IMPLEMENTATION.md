# Bidirectional Setup Flow — Implementation Complete

## Overview

You now have a **bidirectional setup flow** that works both through WhatsApp chat and the web UI. Users can set up the system through either channel, with automatic fallback and guidance.

## Architecture

### When User Sends First Message

```
WhatsApp Message
    ↓
[Webhook Receiver]
    ↓
[Parse & Filter] (DPDP: drop groups without @agent)
    ↓
[Get/Create User]
    ↓
[Check Setup Status]
    ├─ Missing Google OAuth?
    │  ├─ Send setup prompt with options
    │  └─ If message is "SETUP"/"OAUTH"/"STATUS" → handle specially
    ├─ Setup complete?
    │  └─ Dispatch to Hermes Agent
    └─ Return response
```

## Files Modified

### [webhooks.py](backend/app/api/webhooks.py)
**Completely rewritten for clarity and bidirectional setup support.**

Key functions:
- `_parse_evolution_event()` — Normalize Evolution API payloads
- `_get_or_create_user()` — Auto-create user on first message  
- `evolution_webhook()` — Main handler with setup logic
- `evolution_qr_webhook()` — QR code caching

Key behavior:
- Intercepts setup commands ("SETUP", "OAUTH", "STATUS") BEFORE Hermes dispatch
- If user incomplete setup → send guidance with multiple options
- If user sends setup command → handle via `setup_service.handle_setup_command()`
- Otherwise → async task to Hermes

### [setup_service.py](backend/app/services/setup_service.py) (NEW)
**Chat-based setup orchestration.**

```python
async def check_setup_status(db, user) -> str:
    """Returns: 'awaiting_oauth', 'awaiting_whatsapp', or 'ready'"""

async def handle_setup_command(db, user, command: str) -> str:
    """Handles 'SETUP', 'OAUTH', 'STATUS' commands"""
    # Returns helpful messages with links
```

### [oauth.py](backend/app/api/oauth.py)
**Added one-click OAuth redirect.**

New endpoint:
```python
@router.get("/oauth/authorize")
async def oauth_authorize(state: str = None) -> RedirectResponse:
    """Redirects directly to Google OAuth consent screen"""
```

This allows users to:
- Visit one URL: `http://localhost:8000/oauth/authorize`
- Get redirected to Google login
- Authenticate
- Get redirected back to success page
- Tokens automatically stored in database

## User Experience

### Scenario 1: Setup via WhatsApp Chat

**User → Agent:**
```
Hi, I want to use the assistant
```

**Agent → User (Auto):**
```
🔧 Setup Required

To use the AI assistant, you need to authenticate with Google to access 
your Calendar, Drive, Docs, and Sheets.

Visit: http://localhost:8000/setup

Or reply:
- 'OAUTH' for Google authentication link
- 'STATUS' to check setup progress
- 'SETUP' to return to setup page
```

**User → Agent:**
```
OAUTH
```

**Agent → User:**
```
Visit http://localhost:8000/oauth/authorize to authenticate with Google.
```

User clicks link → authenticates with Google → tokens stored → can now use agent

---

### Scenario 2: Setup via Web UI

1. User visits `http://localhost:8000/setup`
2. Sees three steps:
   - **Step 1**: Scan QR code with WhatsApp (links device)
   - **Step 2**: Click "Authenticate with Google" button
   - **Step 3**: All set!
3. QR code auto-refreshes every 15 seconds
4. After Google OAuth → setup complete

---

### Scenario 3: Fallback When Web Connection Fails

If user can't access web UI or WhatsApp QR isn't connecting:

**User can still set up via WhatsApp chat alone:**
- "OAUTH" → gets link to Google auth
- "STATUS" → checks what's still needed
- Once tokens stored → can chat with agent

## How It Works

### Step 1: User Sends Message

```python
# webhooks.py - evolution_webhook()
parsed = _parse_evolution_event(payload)
if not parsed:
    return {"status": "ignored"}

# Get or create user
async with AsyncSessionLocal() as db:
    user = await _get_or_create_user(db, parsed["sender_phone"])
    needs_oauth = user.google_access_token_enc is None

    # If incomplete setup and message is a command
    if needs_oauth:
        cmd_msg = parsed["message_text"].strip().upper()
        if cmd_msg in ("SETUP", "OAUTH", "STATUS"):
            response = await handle_setup_command(db, user, cmd_msg)
            await whatsapp_service.send_text(parsed["sender_phone"], response)
            return {"status": "setup_handled"}
```

### Step 2: Setup Command Handler

```python
# setup_service.py
async def handle_setup_command(db, user, command: str) -> str:
    cmd = command.strip().upper()
    
    if cmd == "STATUS":
        status = await check_setup_status(db, user)
        return f"Setup Status: {status}\n{SETUP_STATES[status]}"
    
    if cmd == "OAUTH":
        return "Visit http://localhost:8000/oauth/authorize to authenticate with Google."
    
    if cmd == "SETUP":
        return "Visit http://localhost:8000/setup to complete setup."
```

### Step 3: OAuth Redirect

```python
# oauth.py
@router.get("/oauth/authorize")
async def oauth_authorize(state: str = None) -> RedirectResponse:
    oauth_state = state or secrets.token_urlsafe(24)
    owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
    await cache_set(f"oauth_state:{oauth_state}", owner_phone, ttl_seconds=600)
    
    auth_url = build_authorization_url(oauth_state)
    return RedirectResponse(url=auth_url)
```

## Privacy & Security

✅ **DPDP Compliance**: Group messages without `@agent` mention are dropped at webhook layer  
✅ **State Validation**: OAuth state tokens stored in Redis, validated on callback  
✅ **Encrypted Tokens**: Google credentials encrypted before storing in database  
✅ **Phone-Based Identity**: Users identified by WhatsApp phone number  
✅ **Owner-Only Tokens**: OAuth always stores tokens for the owner account

## Testing Checklist

After deployment, verify:

- [ ] **Chat Setup**: Send "OAUTH" → bot replies with link
- [ ] **Status Check**: Send "STATUS" → bot shows setup progress
- [ ] **Web Setup**: Visit http://localhost:8000/setup → see QR + Google button
- [ ] **QR Scanning**: Scan QR with WhatsApp → should show success
- [ ] **OAuth Flow**: Click Google button → redirected to Google login
- [ ] **After Setup**: Send normal message → dispatches to Hermes
- [ ] **Privacy**: Send message in group (no @agent) → silently dropped

## Known Limitations

⚠️ **Single Owner**: System currently set up for one owner account  
⚠️ **WhatsApp-Only**: QR auth only works with WhatsApp Linked Devices  
⚠️ **Web UI Optional**: Setup works without web UI (pure chat-based)  
⚠️ **Timeout**: OAuth state tokens expire after 10 minutes

## Next Steps

If issues with setup:

1. **QR Not Generating**: Check Evolution API is connected
2. **OAuth Not Working**: Verify `google_service_account.json` credentials
3. **Commands Not Recognized**: Check webhook is receiving messages (logs show "dispatched_to_hermes")
4. **Setup Prompt Not Sent**: Verify `OWNER_WA_PHONE` matches your phone number

## Architecture Decisions

This implementation uses:

- **FastAPI Webhook Handler**: Receives messages from Evolution API
- **Async Task Dispatch**: Non-blocking dispatch to Hermes via background task
- **Redis Caching**: QR codes and OAuth state tokens
- **PostgreSQL**: User credentials, setup status, tokens
- **Hermes Brain**: Handles all agent logic (once setup complete)
- **LiteLLM Router**: Model fallback chain (GitHub → Gemini → Groq → etc.)

This enables:
- **Endless Possibilities**: Add new services by adding MCP tools, not changing webhook
- **Resilient Setup**: Works even if web connections fail
- **User-Friendly**: Multiple ways to set up (web UI, chat commands, direct link)
