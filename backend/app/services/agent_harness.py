"""FastAPI Harness for Hermes Agent.

Middleman between the intake pipeline and Hermes' OpenAI-compatible endpoint.
Owns: system-prompt assembly (identity/time/mode/connectors), group-privacy
guardrails, transport retries (#8), and reply redaction. Delivery of replies
belongs to the outbound seam (app/outbound), not here.
"""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import logging
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import User
from app.services.preferences_service import preferences_service
from app.services.connector_service import connector_service

logger = logging.getLogger(__name__)

HERMES_API_URL = f"{settings.HERMES_BASE_URL.rstrip('/')}/v1/chat/completions"
_DISPATCH_ATTEMPTS = 2          # #8: one retry for transient failures
_DISPATCH_RETRY_BACKOFF_S = 1.0


def _finalize_reply(content: str, *, in_group: bool) -> str:
    """Apply group-privacy regex redaction to reply content bound for a group.

    Layer 2 of Group Privacy Mode; layer 1 is the system-prompt directive.
    """
    if in_group:
        from app.services.group_privacy_service import redact
        return redact(content)
    return content


async def dispatch_to_hermes(
    session_id: str,
    message_text: str,
    system_prompt: str | None = None,
    use_agent: bool = False,
) -> dict | None:
    """Send the user's message to Hermes.

    Returns the completion payload on success, or None on failure — callers
    (MessagePipeline) turn None into a user-facing fallback reply (#8).
    Never raises for transport/HTTP problems.
    """
    if not message_text.strip():
        return None

    # Retrieve bot identity and active integrations dynamically from DB
    bot_name = "Jarvis"
    owner_name = "You (Owner)"
    bot_mode = "self_chat"
    active_connections: list[str] = []

    async with AsyncSessionLocal() as db:
        try:
            owner_res = await db.execute(select(User).where(User.is_owner == True))  # noqa: E712
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

    # Local current time for the model's benefit
    try:
        timezone_str = getattr(settings, "TIMEZONE", "Asia/Kolkata")
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_str))
        current_time_str = now_local.strftime("%A, %d %B %Y, %I:%M %p %Z")
    except Exception:
        current_time_str = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")

    omniwa_os_context = (
        f"[SYSTEM IDENTITY & ENVIRONMENT CONTEXT]\n"
        f"You are the brain of omniWA, a production-grade WhatsApp-native AI Operating System.\n"
        f"Your configured identity name is: {bot_name}\n"
        f"You are conversing with the owner/user named: {owner_name}\n"
        f"Current system date and time: {current_time_str}\n"
        f"Operating relationship mode: {bot_mode}\n\n"
        f"[ACTIVE SYSTEM CONNECTIONS]\n"
        f"{chr(10).join(active_connections) if active_connections else '- Google Workspace: DISCONNECTED'}\n\n"
        f"Note: If the user requests actions for a DISCONNECTED tool, direct them to link it in their "
        f"omniWA web dashboard. Do NOT guide them through manual CLI or local setup flows."
    )

    if system_prompt:
        final_system_prompt = f"{omniwa_os_context}\n\n[ADDITIONAL CONTEXT]\n{system_prompt}"
    else:
        final_system_prompt = omniwa_os_context

    # Group Privacy Mode guardrail
    from app.services.group_privacy_service import is_group_chat

    in_group = is_group_chat(session_id)
    if in_group:
        from app.services.group_privacy_service import build_group_privacy_directive

        final_system_prompt = f"{final_system_prompt}\n\n{build_group_privacy_directive()}"

    headers = {
        "Content-Type": "application/json",
        "X-Hermes-Session-Id": session_id,
        "Authorization": f"Bearer {settings.HERMES_API_KEY}" if getattr(settings, "HERMES_API_KEY", None) else "Bearer none",
    }

    payload = {
        "model": getattr(settings, "HERMES_MODEL", "hermes-llm"),  # LiteLLM routing
        "messages": [
            {"role": "system", "content": final_system_prompt},
            {"role": "user", "content": message_text},
        ],
    }

    data: dict | None = None
    last_error: Exception | None = None

    for attempt in range(1, _DISPATCH_ATTEMPTS + 1):
        try:
            logger.info("Dispatching to Hermes for session: %s (attempt %d)", session_id, attempt)
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    HERMES_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                )
                response.raise_for_status()
            data = response.json()
            break
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            transient = 500 <= status < 600
            last_error = exc
            if transient and attempt < _DISPATCH_ATTEMPTS:
                logger.warning("Hermes %s on attempt %d — retrying", status, attempt)
                await asyncio.sleep(_DISPATCH_RETRY_BACKOFF_S)
                continue
            logger.error("Hermes dispatch failed for %s (HTTP %s): %s", session_id, status, exc)
            return None
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            if attempt < _DISPATCH_ATTEMPTS:
                logger.warning("Hermes transport error on attempt %d — retrying: %s", attempt, exc)
                await asyncio.sleep(_DISPATCH_RETRY_BACKOFF_S)
                continue
            logger.error("Failed to communicate with Hermes Agent for %s: %s", session_id, exc)
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let brain failure crash the worker
            logger.error("Unexpected dispatch failure for %s: %s", session_id, exc)
            return None

    if data is None:
        logger.error("Hermes dispatch exhausted retries for %s: %s", session_id, last_error)
        return None

    # If Hermes doesn't use MCP to respond, or uses it and also returns a completion:
    try:
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
            if content:
                # Defense-in-depth: scrub sensitive tokens from replies bound
                # for group chats (prompt guardrail may be bypassed).
                content = _finalize_reply(content, in_group=in_group)
                # v3: Hermes' native Baileys bridge delivers the reply itself
                # (session-id = chat target). omniWA does not send messages.
                logger.info("Reply for session %s delivered by Hermes bridge", session_id)
    except Exception as e:
        logger.error(f"Failed to process Hermes reply for {session_id}: {e}")

    return data
