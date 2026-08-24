> 📦 **Historical snapshot.** Written before the Aug 2026 v3 intake refactor (ADR-0007),
> Alembic adoption (#9) and the outbound seam. Some findings may already be resolved —
> see CONTEXT.md for current state.

# Setup Flow Testing Guide

## Quick Test Scenarios

### Test 1: Chat-Based Setup (Recommended First)

**Setup Required**: You have the owner's WhatsApp number set in `.env` as `OWNER_WA_PHONE`

**Steps**:

1. Send WhatsApp message to your bot number:
   ```
   Hi
   ```

2. **Expected Response**:
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

3. Reply with:
   ```
   STATUS
   ```

4. **Expected Response**:
   ```
   Setup Status: awaiting_oauth
   Google OAuth required. Visit http://localhost:8000/oauth/authorize to authenticate.
   ```

5. Reply with:
   ```
   OAUTH
   ```

6. **Expected Response**:
   ```
   Visit http://localhost:8000/oauth/authorize to authenticate with Google.
   ```

---

### Test 2: Check Backend Logs

**To see setup commands being intercepted**:

```bash
cd "c:\Users\golla\Documents\Projects\whatsapp agent\Autonomous-Whatsapp-Agent"
docker-compose -f "docker/docker-compose.yml" logs -f backend
```

**Look for**:
- `Dropped group message: No explicit mention` (privacy filter working)
- `dispatched_to_hermes` (normal messages going to Hermes)
- `setup_handled` (setup commands intercepted)
- `awaiting_oauth` (setup prompt sent)

---

### Test 3: Web Setup Page

1. Open browser: `http://localhost:8000/setup`

2. Should see:
   - **Step 1**: "Scan this QR code with your phone"
   - **Step 2**: "Authenticate with Google" button
   - **Step 3**: "Done!"

3. QR code should be loading/displaying

4. Status should say "Waiting for QR scan..."

---

### Test 4: OAuth Redirect

1. Open browser: `http://localhost:8000/oauth/authorize`

2. Should be **redirected immediately** to Google login

3. Sign in with your Google account

4. Grant permissions:
   - Google Calendar
   - Google Drive
   - Google Docs
   - Google Sheets
   - Gmail

5. Should see success message (or redirect back to setup page)

6. Check database:
   ```bash
   docker-compose -f "docker/docker-compose.yml" exec -T postgres \
     psql -U postgres -d whatsapp_calendar -c "SELECT wa_phone, google_access_token_enc IS NOT NULL FROM \"user\""
   ```

   Should show your phone number with `true` (tokens stored)

---

### Test 5: End-to-End (After Setup Complete)

1. After OAuth completes, send a WhatsApp message:
   ```
   What's your name?
   ```

2. **Expected Response**: 
   Hermes should respond with something like:
   ```
   I'm Claude, an AI assistant powered by Hermes Agent and connected to your Google workspace.
   ```

3. Check logs for:
   ```
   dispatched_to_hermes
   ```

---

### Test 6: Privacy Filter (DPDP Compliance)

1. Create a WhatsApp group with your bot

2. Send message in group **without** mentioning @agent:
   ```
   Hello everyone!
   ```

3. Bot should **not respond** (silently dropped)

4. Check logs for:
   ```
   Dropped group message: No explicit mention
   ```

5. Send message **with** @agent mention:
   ```
   Hey @agent, what time is my next meeting?
   ```

6. Bot should respond (or ask for permission if it's a calendar action)

---

## Debugging

### Issue: Bot Not Responding to WhatsApp Messages

**Check**:
1. `docker-compose -f "docker/docker-compose.yml" logs -f backend` for errors
2. Verify Evolution API webhook is configured correctly
3. Check `OWNER_WA_PHONE` matches your phone (with country code, e.g., +1234567890)
4. Verify webhook receives `messages.upsert` events

### Issue: OAUTH Button Not Working

**Check**:
1. Verify `google_service_account.json` exists and has valid credentials
2. Check that Google OAuth scope includes required APIs
3. Look for errors in backend logs: `Token exchange failed`

### Issue: QR Code Not Displaying

**Check**:
1. `curl http://localhost:8000/setup/qr-status` should return `{"has_qr": true, ...}`
2. Verify Evolution API is connected and in "connecting" state
3. Check webhook is receiving QRCODE_UPDATED events
4. Look at backend logs for: `QR code cached`

### Issue: Setup Commands Not Recognized

**Check**:
1. Make sure message is exactly: "SETUP", "OAUTH", or "STATUS" (uppercase)
2. Check that `setup_service.py` is in `backend/app/services/`
3. Verify no syntax errors: `python -m py_compile backend/app/services/setup_service.py`
4. Check backend logs for imports

---

## Manual Database Checks

### See all users:
```bash
docker-compose -f "docker/docker-compose.yml" exec -T postgres \
  psql -U postgres -d whatsapp_calendar -c "SELECT * FROM \"user\""
```

### See if user has Google tokens:
```bash
docker-compose -f "docker/docker-compose.yml" exec -T postgres \
  psql -U postgres -d whatsapp_calendar -c \
  "SELECT wa_phone, google_access_token_enc IS NOT NULL as has_oauth, created_at FROM \"user\""
```

### See recent audit logs:
```bash
docker-compose -f "docker/docker-compose.yml" exec -T postgres \
  psql -U postgres -d whatsapp_calendar -c \
  "SELECT user_id, action, resource_type, status, created_at FROM audit_log ORDER BY created_at DESC LIMIT 10"
```

---

## Successful Setup Flow Indicators

✅ User created on first WhatsApp message  
✅ Setup prompt sent automatically  
✅ "OAUTH" command returns link  
✅ "STATUS" command shows progress  
✅ OAuth redirect to Google works  
✅ Tokens stored after Google auth  
✅ Normal messages dispatch to Hermes  
✅ Group messages without @agent are dropped  

---

## Architecture Reminders

- **Webhook** catches all Evolution API events
- **Before Hermes**: Setup check, privacy filter, command handling  
- **After Setup**: All messages go to Hermes Agent
- **Hermes** has access to MCP tools (calendar, http_request, send_whatsapp, etc.)
- **Tokens** are encrypted at rest in PostgreSQL
- **Redis** stores temporary data (QR codes, OAuth state) with TTLs
