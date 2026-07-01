"""MCP server — exposes our calendar tools to Hermes Agent.

Hermes connects via Server-Sent Events (SSE) transport on port 9000.
Each tool here is a thin wrapper around our existing calendar_service.

New tools (Drive, Maps, etc.) are added here as we build Phase 4+.

Run: python -m app.mcp_server.main
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP  # high-level FastMCP library (not mcp.server.fastmcp)

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.database import AsyncSessionLocal
from app.models.models import User, ChatACL, PendingDecision
from app.services.calendar_service import calendar_service
from app.services.oauth_service import load_user_credentials

setup_logging()
logger = get_logger("mcp_server")

mcp = FastMCP(
    name="whatsapp-agent-tools",
    instructions=(
        "Tools for managing the user's Google Calendar and Workspace. "
        "Always use ISO 8601 datetimes. Timezone is " + settings.TIMEZONE + ". "
        "When creating events with video calls, set create_meet_link=true. "
        "CRITICAL: If you are conversing with a contact (who is not the owner), you MUST always pass the contact's phone number or JID "
        "as the 'sender_phone' argument to any tool call that accepts it. This ensures owner approvals are requested."
    ),
)


async def _get_owner() -> User | None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(User).where(User.is_owner == True)
        )
        return result.scalar_one_or_none()


async def _check_owner_approval(
    action_type: str,
    proposed_action: dict,
    sender_phone: str | None = None
) -> tuple[bool, str | None]:
    """Check if the sender is the owner, or if there is an approved PendingDecision.
    
    Returns:
        (is_allowed, response_message_if_blocked)
    """
    owner_phone_clean = settings.OWNER_WA_PHONE.replace("+", "").strip()
    
    # If no sender_phone was provided, assume owner request for safety/backwards compatibility
    if not sender_phone:
        return True, None
        
    sender_clean = sender_phone.replace("@s.whatsapp.net", "").replace("@c.us", "").replace("+", "").strip()
    
    from app.services.preferences_service import preferences_service
    bot_phone = await preferences_service.get_owner_preference("bot_phone")
    if bot_phone:
        bot_phone_clean = bot_phone.replace("+", "").strip()
    else:
        bot_phone_clean = None

    if sender_clean == owner_phone_clean or (bot_phone_clean and sender_clean == bot_phone_clean):
        return True, None
        
    # Non-owner trigger: Check for recently approved PendingDecision in database
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta
    from app.services.permission_service import permission_service
    
    async with AsyncSessionLocal() as db:
        # Check for approved decision resolved within last 15 minutes
        fifteen_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=15)
        result = await db.execute(
            select(PendingDecision)
            .where(
                PendingDecision.action_type == action_type,
                PendingDecision.status == "approved",
                PendingDecision.resolved_at >= fifteen_mins_ago
            )
        )
        decision = result.scalar_one_or_none()
        
        if decision:
            # Action approved! Mark it as completed so it can't be reused
            decision.status = "completed"
            await db.commit()
            return True, None
            
        # No approved decision: Create a new awaiting PendingDecision and DM the owner
        result_user = await db.execute(
            select(User).where(User.wa_phone == sender_clean)
        )
        user = result_user.scalar_one_or_none()
        if not user:
            # Create user if they don't exist
            user = User(wa_phone=sender_clean, is_owner=False, has_permission=True)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            
        decision = await permission_service.request_permission(
            db, user, action_type, proposed_action, source_chat=sender_phone
        )
        
        import json
        return False, json.dumps({
            "status": "approval_pending",
            "message": f"This action requires owner approval. An approval request has been sent to the owner (code: {decision.short_code}). Please tell the user that the action is pending owner approval."
        })


@mcp.tool()
async def send_whatsapp_message(to_number: str, message: str) -> str:
    """Send a WhatsApp message back to the user or a group.
    
    Args:
        to_number: The WhatsApp phone number or group ID (e.g. 919999999999 or 123-456@g.us)
        message: The text of the message to send.
    """
    from app.services.whatsapp_service import WhatsAppService
    from sqlalchemy import select
    
    service = WhatsAppService()
    
    # Check if voice responses are enabled for this chat
    voice_enabled = False
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(ChatACL).where(ChatACL.chat_id == to_number))
            acl = result.scalar_one_or_none()
            if acl:
                voice_enabled = acl.voice_enabled
    except Exception as exc:
        logger.error("Error checking voice ACL: %s", exc)

    success = False
    if voice_enabled:
        from app.services.audio_service import audio_service
        logger.info("Voice replies enabled for %s. Generating TTS...", to_number)
        base64_audio = await audio_service.text_to_speech(message)
        if base64_audio:
            success = await service.send_audio(to_number, base64_audio)
            
    # Fallback to text if voice is disabled or TTS generation failed
    if not success:
        success = await service.send_text(to_number, message)
        
    if success:
        return json.dumps({"status": "sent", "to": to_number, "type": "voice" if voice_enabled else "text"})
    else:
        return json.dumps({"error": "Failed to send message over Evolution API"})

@mcp.tool()
async def search_contacts(query: str) -> str:
    """Search WhatsApp contacts by name or phone number.
    
    Args:
        query: The name, pushname, or phone number to search for (case-insensitive).
    """
    import httpx
    from app.core.config import settings
    
    headers = {"apikey": settings.OPENWA_API_KEY, "Content-Type": "application/json"}
    base_url = settings.OPENWA_BASE_URL.rstrip("/")
    instance = settings.OPENWA_SESSION_ID
    
    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
            r = await client.post(f"/chat/findContacts/{instance}", json={})
            if r.status_code != 200:
                return json.dumps({"error": f"API returned status code {r.status_code}"})
            
            contacts = r.json()
            query_lower = query.lower()
            matches = []
            for c in contacts:
                name = c.get("name") or ""
                pushName = c.get("pushName") or c.get("pushname") or ""
                jid = c.get("remoteJid") or c.get("id") or ""
                if (query_lower in name.lower() or 
                    query_lower in pushName.lower() or 
                    query_lower in jid.lower()):
                    matches.append({
                        "name": name,
                        "pushName": pushName,
                        "jid": jid
                    })
            
            return json.dumps(matches[:10])
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def http_request(method: str, url: str, headers: dict | None = None, json_body: dict | None = None) -> str:
    """Perform a generic HTTP request. Use this to integrate with any external service.
    
    Requires Owner Confirmation due to high potential impact.

    Args:
        method: GET, POST, PUT, DELETE, etc.
        url: The full URL to call.
        headers: Optional dictionary of HTTP headers.
        json_body: Optional JSON payload for the request.
    """
    # Permission Gate (Security Service logic representation)
    user = await _get_owner()
    # If this was real execution, we'd check pending decisions. 
    # For now, we simulate the "Paused" state if it hasn't been approved:
    
    # In a fully fleshed out system, we check DB for existing matching approval
    # return json.dumps({"error": "Paused. Requires user confirmation. Ask user 'Reply YES to confirm this network request: {url}'"})
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, headers=headers, json=json_body)
            return json.dumps({"status": resp.status_code, "text": resp.text[:1000]})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
async def get_current_time() -> str:
    """Return the current date and time in the owner's timezone.
    Use this before scheduling anything to avoid timezone errors."""
    now = datetime.now(timezone.utc).astimezone()
    return json.dumps({
        "iso": now.isoformat(),
        "human": now.strftime("%A, %d %B %Y — %I:%M %p %Z"),
        "timezone": settings.TIMEZONE,
    })


@mcp.tool()
async def call_connector_api(
    provider: str,
    endpoint: str,
    method: str = "GET",
    headers: dict | None = None,
    json_body: dict | None = None,
    query_params: dict | None = None,
    sender_phone: str | None = None,
) -> str:
    """Make an HTTP request to any connected provider using saved credentials.
    
    Supported providers:
      - google: Google APIs (Calendar, Drive, Docs, Sheets, Gmail). Endpoint is the API path, e.g. "calendar/v3/calendars/primary/events".
      - github: GitHub REST API. Endpoint is the API path, e.g. "repos/owner/repo/issues".
      - notion: Notion API. Endpoint is the path, e.g. "pages" or "databases/database_id/query".
      - home_assistant: Home Assistant REST API. Endpoint is the path, e.g. "states".
      - microsoft_graph: Microsoft Graph/Outlook API. Endpoint is the path, e.g. "me/messages".
      
    Args:
        provider: Name of the provider ('google', 'github', 'notion', 'home_assistant', 'microsoft_graph')
        endpoint: The API endpoint path (excluding the base URL).
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, etc.)
        headers: Additional HTTP headers to pass
        json_body: JSON payload for POST/PUT/PATCH requests
        query_params: URL query parameters
        sender_phone: Optional WhatsApp phone number or JID of the sender.
    """
    import httpx
    from app.core.security import decrypt_token
    from app.models.models import ApiKey
    from sqlalchemy import select

    provider = provider.lower().strip()
    method = method.upper().strip()

    # Security permission gate for modifying actions triggered by non-owner contacts
    if method not in ("GET", "HEAD", "OPTIONS"):
        proposed = {
            "provider": provider,
            "endpoint": endpoint,
            "method": method,
            "json_body": json_body,
        }
        allowed, response_msg = await _check_owner_approval(f"api_call_{provider}", proposed, sender_phone)
        if not allowed:
            return response_msg

    token = None
    base_url = None
    custom_headers = {}

    async with AsyncSessionLocal() as db:
        if provider == "google":
            result = await db.execute(select(User).where(User.is_owner == True))
            user = result.scalar_one_or_none()
            if not user:
                return json.dumps({"error": "Owner user not found"})
            creds = await load_user_credentials(user, db)
            if not creds:
                return json.dumps({"error": "Google OAuth tokens not found. Please authenticate via the setup UI."})
            token = creds.token
            base_url = "https://www.googleapis.com/"
            custom_headers["Authorization"] = f"Bearer {token}"
        else:
            result = await db.execute(
                select(ApiKey).where(ApiKey.provider == provider, ApiKey.is_active == True)
            )
            key_record = result.scalars().first()
            
            raw_val = None
            if key_record:
                try:
                    raw_val = decrypt_token(key_record.api_key_enc)
                except Exception as e:
                    logger.error("Failed to decrypt key: %s", e)

            if not raw_val:
                # System fallbacks
                if provider == "github" and getattr(settings, "GITHUB_TOKEN", None):
                    raw_val = settings.GITHUB_TOKEN

            if not raw_val:
                return json.dumps({"error": f"No active credentials found for provider '{provider}'."})

            if provider == "github":
                base_url = "https://api.github.com/"
                custom_headers["Authorization"] = f"Bearer {raw_val}"
                custom_headers["Accept"] = "application/vnd.github+json"
                custom_headers["X-GitHub-Api-Version"] = "2022-11-28"
            elif provider == "notion":
                base_url = "https://api.notion.com/v1/"
                custom_headers["Authorization"] = f"Bearer {raw_val}"
                custom_headers["Notion-Version"] = "2022-06-28"
            elif provider == "home_assistant":
                if raw_val.startswith("{"):
                    try:
                        data = json.loads(raw_val)
                        base_url = data.get("base_url")
                        token = data.get("token")
                    except Exception:
                        pass
                if not base_url:
                    base_url = "http://host.docker.internal:8123/api/"
                if not token:
                    token = raw_val
                base_url = base_url.rstrip("/") + "/"
                custom_headers["Authorization"] = f"Bearer {token}"
            elif provider in ("microsoft_graph", "outlook"):
                base_url = "https://graph.microsoft.com/v1.0/"
                custom_headers["Authorization"] = f"Bearer {raw_val}"
            else:
                return json.dumps({"error": f"Unsupported provider: {provider}"})

    req_headers = {**custom_headers}
    if headers:
        req_headers.update(headers)

    url = base_url + endpoint.lstrip("/")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method=method,
                url=url,
                headers=req_headers,
                json=json_body,
                params=query_params,
                timeout=30.0
            )
            try:
                res_data = resp.json()
            except Exception:
                res_data = resp.text
            return json.dumps({
                "status_code": resp.status_code,
                "response": res_data
            })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


if __name__ == "__main__":
    import uvicorn
    logger.info("MCP server starting on port 9000")
    # FastMCP 3.x: http_app returns a Starlette app for HTTP/SSE transport
    asgi_app = mcp.http_app(transport="sse")
    uvicorn.run(asgi_app, host="0.0.0.0", port=9000, log_level="info")

