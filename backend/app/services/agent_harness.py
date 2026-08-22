"""FastAPI Harness for Hermes Agent.

This module acts as the middleman between our webhooks and Hermes Agent's OpenAI-compatible
endpoint. It receives valid (permission-passed) incoming messages and POSTs them
to Hermes, ensuring Hermes tracks conversations via X-Hermes-Session-Id.
"""

import httpx
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import User
from app.services.preferences_service import preferences_service
from app.services.connector_service import connector_service

logger = logging.getLogger(__name__)

HERMES_API_URL = f"{settings.HERMES_BASE_URL.rstrip('/')}/v1/chat/completions"

async def dispatch_to_hermes(session_id: str, message_text: str, system_prompt: str | None = None, use_agent: bool = False) -> dict | None:
    """
    Sends the user's message to Hermes Agent. 
    
    Args:
        session_id: The WhatsApp Phone Number (acts as X-Hermes-Session-Id for memory).
        message_text: The incoming message to process.
        system_prompt: Optional system instructions/context.
        use_agent: If True, send response through the agent session instead of primary.
    """
    if not message_text.strip():
        return None

    # Retrieve bot identity and active integrations dynamically from DB
    bot_name = "Jarvis"
    owner_name = "You (Owner)"
    bot_mode = "self_chat"
    active_connections = []

    async with AsyncSessionLocal() as db:
        try:
            owner_res = await db.execute(select(User).where(User.is_owner == True))
            owner = owner_res.scalar_one_or_none()
            if owner:
                bot_name = await preferences_service.get(owner.id, "bot_name", "Jarvis")
                owner_name = await preferences_service.get(owner.id, "owner_name", "You (Owner)")
                bot_mode = await preferences_service.get(owner.id, "bot_mode", "self_chat")
                
                try:
                    status_list = await connector_service.list_connectors_status(owner.id)
                    for conn in status_list:
                        status_str = "CONNECTED" if conn["connected"] else "DISCONNECTED"
                        active_connections.append(f"- {conn['name']}: {status_str}")
                except Exception as e:
                    logger.error("Failed to build connectors system prompt in harness: %s", e)
        except Exception as e:
            logger.error("Failed to load preferences in dispatch_to_hermes: %s", e)

    # Get local current time
    now = datetime.now()
    timezone_str = getattr(settings, "TIMEZONE", "Asia/Kolkata")
    try:
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_str))
        current_time_str = now_local.strftime("%A, %d %B %Y, %I:%M %p %Z")
    except Exception:
        current_time_str = now.strftime("%A, %d %B %Y, %I:%M %p")

    # Build the rich system prompt context for omniWA
    omniwa_os_context = (
        f"[SYSTEM IDENTITY & ENVIRONMENT CONTEXT]\n"
        f"You are the brain of omniWA, a production-grade WhatsApp-native AI Operating System.\n"
        f"Your configured identity name is: {bot_name}\n"
        f"You are conversing with the owner/user named: {owner_name}\n"
        f"Current system date and time: {current_time_str}\n"
        f"Operating relationship mode: {bot_mode}\n\n"
        f"[ACTIVE SYSTEM CONNECTIONS]\n"
        f"{chr(10).join(active_connections) if active_connections else '- Google Workspace: DISCONNECTED'}\n\n"
        f"Note: If the user requests actions for a DISCONNECTED tool, direct them to link it in their omniWA web dashboard. Do NOT guide them through manual CLI or local setup flows."
    )

    if system_prompt:
        final_system_prompt = f"{omniwa_os_context}\n\n[ADDITIONAL CONTEXT]\n{system_prompt}"
    else:
        final_system_prompt = omniwa_os_context

    # Group Privacy Mode: when replying into a WhatsApp group, inject the hard
    # privacy guardrail so the agent never exposes owner-sensitive data there.
    from app.services.group_privacy_service import is_group_chat
    in_group = is_group_chat(session_id)
    if in_group:
        from app.services.group_privacy_service import build_group_privacy_directive
        final_system_prompt = f"{final_system_prompt}\n\n{build_group_privacy_directive()}"

    headers = {
        "Content-Type": "application/json",
        "X-Hermes-Session-Id": session_id,
        "Authorization": f"Bearer {settings.HERMES_API_KEY}" if getattr(settings, 'HERMES_API_KEY', None) else "Bearer none"
    }

    messages = [
        {"role": "system", "content": final_system_prompt},
        {"role": "user", "content": message_text}
    ]

    payload = {
        "model": getattr(settings, "HERMES_MODEL", "hermes-llm"),  # LiteLLM routing
        "messages": messages
    }


    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Dispatching to Hermes for session: {session_id} (use_agent: {use_agent})")
            response = await client.post(
                HERMES_API_URL, 
                json=payload, 
                headers=headers,
                timeout=120.0
            )
            response.raise_for_status()
            
            data = response.json()
            # If Hermes doesn't use MCP to respond, or uses it and also returns a completion:
            try:
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        # Defense-in-depth: scrub sensitive tokens from replies
                        # bound for group chats (prompt guardrail may be bypassed).
                        if in_group:
                            from app.services.group_privacy_service import redact
                            content = redact(content)
                        if getattr(settings, "HERMES_OWNS_WHATSAPP", False):
                            # Hermes' native Baileys bridge delivers the reply itself
                            # (session-id = chat target). omniWA does NOT call Evolution.
                            logger.info(
                                "HERMES_OWNS_WHATSAPP=true — reply for session %s delivered by Hermes bridge",
                                session_id,
                            )
                        elif use_agent:
                            from app.services.agent_instance_service import agent_instance_service
                            await agent_instance_service.send_via_agent(session_id, content)
                        else:
                            from app.services.whatsapp_service import whatsapp_service
                            await whatsapp_service.send_text(session_id, content)
            except Exception as e:
                logger.error(f"Failed to send Hermes reply back to WhatsApp: {e}")
            return data
            
    except httpx.HTTPError as exc:
        logger.error(f"Failed to communicate with Hermes Agent: {exc}")
        return None
