"""Evolution API webhook receiver — handles WhatsApp messages.

Message flow:
  1. WhatsApp user sends message
  2. Evolution API pushes to /webhook/openwa
  3. Check if sender needs setup (missing Google OAuth)
  4. If setup needed and message is "SETUP"/"OAUTH"/"STATUS", handle specially
  5. Otherwise, forward to Hermes Agent
"""
import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_openwa_signature
from app.db.database import AsyncSessionLocal
from app.db.redis_client import cache_get, cache_set, check_idempotency, check_rate_limit
from app.models.models import User
from app.services.whatsapp_service import whatsapp_service, normalize_phone_number

router = APIRouter()
logger = get_logger(__name__)

chat_queues: dict[str, asyncio.Queue] = {}
chat_workers: dict[str, asyncio.Task] = {}


def _event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("event") or "").lower().replace("_", ".")


async def _store_qr_from_payload(payload: dict[str, Any]) -> bool:
    """Extract and cache QR code data."""
    def _extract_qr(value: Any) -> str:
        if isinstance(value, str):
            return value if ("base64" in value or len(value) > 100) else ""
        if isinstance(value, dict):
            for key in ("base64", "qrcode", "qr", "code"):
                found = _extract_qr(value.get(key))
                if found:
                    return found
            for child in value.values():
                found = _extract_qr(child)
                if found:
                    return found
        return ""
    
    qr_data = _extract_qr(payload.get("data") or payload)
    if not qr_data:
        return False
    await cache_set("whatsapp:qr_code", qr_data, ttl_seconds=180)
    logger.info("QR code cached (180s TTL)")
    return True


def _extract_text(message: dict) -> str:
    """Extract text from Evolution API message."""
    return (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or (message.get("imageMessage") or {}).get("caption")
        or ""
    ).strip()


async def _parse_evolution_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse Evolution API message event into normalized format."""
    event = _event_name(payload)
    if event not in ("messages.upsert", "send.message"):
        return None

    data = payload.get("data") or {}
    key = data.get("key") or {}

    remote_jid = key.get("remoteJid") or ""
    if not remote_jid:
        return None

    from_me = key.get("fromMe") or False
    if event == "send.message":
        from_me = True

    sender_phone_from_jid = (
        remote_jid
        .replace("@s.whatsapp.net", "")
        .replace("@c.us", "")
        .lstrip("+")
    )
    
    # Try to get bot_phone from cache or query it from Evolution API based on instance
    instance = payload.get("instance")
    if instance == "agent-session":
        bot_phone = await cache_get("whatsapp:agent_bot_phone")
        if not bot_phone:
            from app.services.agent_instance_service import agent_instance_service
            bot_phone = await agent_instance_service.get_agent_phone()
            if bot_phone:
                await cache_set("whatsapp:agent_bot_phone", bot_phone, ttl_seconds=86400)
    else:
        bot_phone = await cache_get("whatsapp:bot_phone")
        if not bot_phone:
            bot_phone = await whatsapp_service.get_bot_phone()
            if bot_phone:
                await cache_set("whatsapp:bot_phone", bot_phone, ttl_seconds=86400)
    
    if not bot_phone:
        bot_phone = (
            payload.get("sender") or ""
        ).replace("@s.whatsapp.net", "").replace("@c.us", "").lstrip("+")

    from app.services.preferences_service import preferences_service
    bot_mode = await preferences_service.get_owner_preference("bot_mode", settings.BOT_RELATIONSHIP_MODE)

    if bot_mode == "self_chat":
        is_self_chat = (sender_phone_from_jid == bot_phone)
    else:
        is_self_chat = False

    if from_me and not is_self_chat:
        return None  # Ignore outbound messages to other people

    msg_obj = data.get("message") or {}
    body = _extract_text(msg_obj)
    is_audio = "audioMessage" in msg_obj

    if not body and not is_audio:
        return None

    context_info = None
    if isinstance(msg_obj, dict):
        for k, v in msg_obj.items():
            if isinstance(v, dict) and "contextInfo" in v:
                context_info = v["contextInfo"]
                break
        if not context_info:
            context_info = msg_obj.get("contextInfo") or {}
    else:
        context_info = {}

    quoted_text = ""
    if context_info:
        quoted_msg = context_info.get("quotedMessage")
        if isinstance(quoted_msg, dict):
            quoted_text = _extract_text(quoted_msg)

    is_group = "@g.us" in remote_jid
    participant = data.get("participant") or ""

    if is_group:
        sender_jid = participant or remote_jid
        chat_id = remote_jid
    else:
        sender_jid = remote_jid
        chat_id = remote_jid

    sender_phone = (
        sender_jid
        .replace("@s.whatsapp.net", "")
        .replace("@c.us", "")
        .lstrip("+")
    )

    push_name = data.get("pushName") or ""

    return {
        "sender_phone": sender_phone,
        "chat_id": chat_id,
        "is_group": is_group,
        "group_id": chat_id if is_group else "",
        "message_text": body or "[Voice Message]",
        "message_id": key.get("id", ""),
        "timestamp": str(data.get("messageTimestamp", "")),
        "is_audio": is_audio,
        "push_name": push_name,
        "quoted_text": quoted_text,
        "bot_phone": bot_phone,
        "instance": instance,
        "bot_mode": bot_mode,
    }


async def _get_or_create_user(db: AsyncSession, wa_phone: str, display_name: str | None = None) -> User:
    """Get or create user by WhatsApp phone number."""
    result = await db.execute(select(User).where(User.wa_phone == wa_phone))
    user = result.scalar_one_or_none()
    if user:
        if display_name and (not user.display_name or user.display_name == f"User {user.wa_phone[-4:]}"):
            user.display_name = display_name
            await db.commit()
            await db.refresh(user)
        return user

    # Check if any owner exists in the database
    owner_result = await db.execute(select(User).where(User.is_owner == True))
    has_owner = owner_result.scalar_one_or_none() is not None
    
    if has_owner:
        is_owner = False
    else:
        is_owner = wa_phone == settings.OWNER_WA_PHONE.lstrip("+")
        
    user = User(wa_phone=wa_phone, is_owner=is_owner, timezone=settings.TIMEZONE, display_name=display_name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(f"Created user {wa_phone} (owner={is_owner}, display_name={display_name})")
    return user


@router.post("/webhook/qr")
async def evolution_qr_webhook(request: Request) -> dict[str, str]:
    """Dedicated QR code update webhook."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}
    return {"status": "ok" if await _store_qr_from_payload(payload) else "no_qr"}


async def _send_reply(parsed: dict[str, Any], to: str, text: str) -> None:
    """Send a reply to the sender/chat using the correct WhatsApp instance session."""
    if parsed.get("instance") == "agent-session":
        from app.services.agent_instance_service import agent_instance_service
        await agent_instance_service.send_via_agent(to, text)
    else:
        await whatsapp_service.send_text(to, text)


async def _chat_worker(chat_id: str, queue: asyncio.Queue):
    """Processes messages sequentially for a specific chat_id."""
    logger.info(f"Starting sequential queue worker for chat {chat_id}")
    try:
        while True:
            parsed = await queue.get()
            try:
                # 1. Handle incoming audio transcription if it is audio
                if parsed.get("is_audio"):
                    logger.info("Received audio message %s. Fetching base64...", parsed["message_id"])
                    base64_audio = await whatsapp_service.download_media(parsed["message_id"])
                    if base64_audio:
                        from app.services.audio_service import audio_service
                        transcription = await audio_service.transcribe_audio(base64_audio)
                        if transcription:
                            parsed["message_text"] = transcription
                            logger.info("Voice message transcribed: %s", transcription)

                # 2. DPDP Privacy Filter: Drop group messages without explicit mention
                if parsed["is_group"]:
                    body_lower = parsed["message_text"].lower()
                    if "@agent" not in body_lower and getattr(settings, "BOT_NAME", "assistant").lower() not in body_lower:
                        logger.info(f"Dropped group message: No explicit mention")
                        continue


                # 3. Get or create user
                async with AsyncSessionLocal() as db:
                    user = await _get_or_create_user(db, parsed["sender_phone"], display_name=parsed.get("push_name"))
                    
                    bot_phone = parsed.get("bot_phone")
                    if not bot_phone:
                        bot_phone = await cache_get("whatsapp:bot_phone")
                        if not bot_phone:
                            bot_phone = await whatsapp_service.get_bot_phone()
                            if bot_phone:
                                await cache_set("whatsapp:bot_phone", bot_phone, ttl_seconds=86400)
                    
                    from app.services.preferences_service import preferences_service
                    # Fetch owner's configured bot_phone from preferences directly to support dual number mode owner resolution
                    owner_bot_phone = await preferences_service.get_owner_preference("bot_phone")
                    if owner_bot_phone:
                        owner_bot_phone = owner_bot_phone.lstrip("+")
                    
                    bot_mode = parsed.get("bot_mode")
                    is_owner = user.is_owner
                    if bot_mode != "dual_number" and owner_bot_phone and user.wa_phone == owner_bot_phone:
                        is_owner = True
                    
                    # A. Monitored Chats Check
                    chat_clean = parsed["chat_id"].split("@")[0].split(":")[0].lstrip("+")
                    chat_monitored = False
                    if is_owner and not parsed["is_group"]:
                        chat_monitored = True
                    else:
                        # Check if the chat ID itself is whitelisted
                        chat_result = await db.execute(select(User).where(User.wa_phone == chat_clean))
                        chat_config = chat_result.scalar_one_or_none()
                        if chat_config and chat_config.has_permission:
                            chat_monitored = True
                            
                    if not chat_monitored:
                        logger.info(f"Dropped message: chat {parsed['chat_id']} (clean: {chat_clean}) is not configured/monitored.")
                        continue

                    # B. Trusted vs Untrusted Sender Check
                    if not is_owner and user.trust_level == "untrusted":
                        logger.info(f"Dropped message: sender {parsed['sender_phone']} is untrusted.")
                        continue

                    # Intercept owner approvals for pending decisions
                    if is_owner:
                        from app.services.permission_service import permission_service
                        resolved = await permission_service.try_resolve(db, parsed["message_text"])
                        if resolved:
                            await _send_reply(
                                parsed,
                                parsed["sender_phone"],
                                f"✅ Decision {resolved.short_code} has been {resolved.status}."
                            )
                            if resolved.status == "approved" and resolved.source_chat:
                                notify_msg = f"🔔 The owner has approved the requested action: {resolved.action_type.replace('_', ' ').title()}."
                                await whatsapp_service.send_text(resolved.source_chat, notify_msg)
                            continue

                    # Intercept owner slash commands
                    if is_owner and parsed["message_text"].strip().startswith("/"):
                        from app.services.command_parser import handle_command
                        response = await handle_command(db, user.id, parsed["message_text"])
                        if response:
                            await db.commit()  # commit audit logs
                            await _send_reply(parsed, parsed["sender_phone"], response)
                        continue

                    # If owner explicitly requests setup flow commands, handle them
                    if is_owner:
                        cmd_msg = parsed["message_text"].strip().upper()
                        if cmd_msg in ("SETUP", "OAUTH", "STATUS"):
                            from app.services.setup_service import handle_setup_command
                            response = await handle_setup_command(db, user, cmd_msg)
                            if response:
                                await _send_reply(parsed, parsed["sender_phone"], response)
                            continue

                    # Evaluate ACL rules and quiet hours — owners always bypass this
                    if not is_owner:
                        from app.services.security_service import security_service
                        acl_action = await security_service.evaluate(db, parsed["chat_id"], parsed["sender_phone"], user)
                        if acl_action == "block":
                            logger.info(f"Ignored message {parsed['message_id']} due to block ACL rule.")
                            continue
                        elif acl_action == "silent_log":
                            # Also check has_permission flag — whitelisted contacts/groups should always pass
                            chat_has_permission = chat_config.has_permission if chat_config else False
                            if not chat_has_permission and not user.has_permission:
                                logger.info(f"Logged message {parsed['message_id']} silently due to quiet hours or silence ACL rule.")
                                continue

                # Forward to Hermes Agent
                from app.services.agent_harness import dispatch_to_hermes
                system_prompt = None

                # Check for reply context prefixing
                message_text = parsed["message_text"]
                quoted_text = parsed.get("quoted_text")
                if quoted_text:
                    final_text = f'[Replying to: "{quoted_text}"] {message_text}'
                else:
                    final_text = message_text

                # If it's a group, prefix with sender info so Hermes knows who is talking
                if parsed["is_group"]:
                    sender_info = f"{parsed['push_name']} (+{parsed['sender_phone']})" if parsed.get("push_name") else f"+{parsed['sender_phone']}"
                    final_text = f"[{sender_info}]: {final_text}"

                logger.info(f"Forwarding message {parsed['message_id']} to Hermes: {final_text}")
                # Pass chat_id (group JID or DM phone) as the session ID to maintain chat context and reply to the group
                use_agent = (parsed.get("instance") == "agent-session")
                await dispatch_to_hermes(parsed["chat_id"], final_text, system_prompt=system_prompt, use_agent=use_agent)

            except Exception as inner_exc:
                logger.error(f"Error handling message {parsed.get('message_id')}: {inner_exc}", exc_info=True)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logger.info(f"Worker for chat {chat_id} cancelled")
    except Exception as exc:
        logger.error(f"Worker for chat {chat_id} crashed: {exc}", exc_info=True)
    finally:
        chat_queues.pop(chat_id, None)
        chat_workers.pop(chat_id, None)


@router.post("/webhook/openwa")
async def evolution_webhook(request: Request) -> dict[str, str]:
    """Main webhook for Evolution API messages."""
    raw_body = await request.body()

    # Verify HMAC signature
    signature = (
        request.headers.get("X-Evolution-Signature")
        or request.headers.get("x-evolution-signature")
    )
    if not verify_openwa_signature(raw_body, signature):
        logger.warning("Webhook signature verification failed")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
        logger.info("Webhook payload: %s", payload)
    except Exception as exc:
        logger.error(f"Bad JSON in webhook: {exc}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    # Handle special events
    event = _event_name(payload)
    if event == "qrcode.updated":
        return {"status": "qr_updated" if await _store_qr_from_payload(payload) else "no_qr"}
    if event == "connection.update":
        state = (payload.get("data") or {}).get("state") or (payload.get("data") or {}).get("status")
        if state:
            await cache_set("whatsapp:connection_state", str(state), ttl_seconds=300)
        return {"status": "connection_update", "state": str(state or "")}

    # Check idempotency
    idem_key = (
        payload.get("idempotencyKey")
        or (((payload.get("data") or {}).get("key") or {}).get("id"))
        or ""
    )
    instance = payload.get("instance") or "default"
    if idem_key and not await check_idempotency(f"{instance}:{idem_key}"):
        return {"status": "duplicate"}

    # Parse message
    parsed = await _parse_evolution_event(payload)
    if not parsed:
        return {"status": "ignored", "reason": "not_parseable_or_from_me"}

    # In dual_number mode, we strictly ignore all message events from the primary owner session (my-session)
    # to prevent loop-backs and double processing. The bot only responds on the agent-session.
    bot_mode = parsed.get("bot_mode")
    instance = payload.get("instance")
    if bot_mode == "dual_number" and instance != "agent-session":
        return {"status": "ignored_primary_session_in_dual_mode", "instance": instance}

    # Resolve owner phone number dynamically
    owner_phone = await cache_get("whatsapp:owner_phone")
    if not owner_phone:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(User).where(User.is_owner == True))
                owner_user = result.scalar_one_or_none()
                if owner_user:
                    owner_phone = owner_user.wa_phone
                    await cache_set("whatsapp:owner_phone", owner_phone, ttl_seconds=300)
        except Exception as e:
            logger.error("Failed to fetch owner from database: %s", e)

    if not owner_phone:
        owner_phone = settings.OWNER_WA_PHONE

    owner_clean = owner_phone.lstrip("+")

    # Strict check for agent-session: ONLY owner chat allowed
    instance = payload.get("instance")
    if instance == "agent-session":
        chat_clean = parsed["chat_id"].split("@")[0].split(":")[0].lstrip("+")
        if chat_clean != owner_clean:
            logger.info("Ignoring agent-session webhook event: Target chat %s is not the owner chat %s", chat_clean, owner_clean)
            return {"status": "ignored_non_owner_agent_chat", "chat_id": parsed["chat_id"]}

    # Check if this message was sent by our API (to prevent infinite loops)
    if await cache_get(f"sent_message:{parsed['message_id']}"):
        logger.info(f"Ignoring message {parsed['message_id']} sent by our own bot API")
        return {"status": "ignored_self_api_send", "message_id": parsed["message_id"]}

    # Rate limit check
    rate_limit_ok = await check_rate_limit(parsed["sender_phone"])
    if not rate_limit_ok:
        logger.warning(f"User {parsed['sender_phone']} rate-limited")
        if parsed["sender_phone"] == owner_clean:
            alert_msg = "⚠️ *System Alert*: You are sending messages too quickly. Please wait a moment before sending more messages."
            if parsed.get("instance") == "agent-session":
                from app.services.agent_instance_service import agent_instance_service
                await agent_instance_service.send_via_agent(parsed["sender_phone"], alert_msg)
            else:
                await whatsapp_service.send_text(parsed["sender_phone"], alert_msg)
        return {"status": "rate_limited", "message_id": parsed["message_id"]}

    # Queue message processing sequentially
    chat_id = parsed["chat_id"]
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(_chat_worker(chat_id, chat_queues[chat_id]))

    q = chat_queues[chat_id]
    if q.qsize() >= 5:
        logger.warning(f"Queue size for chat {chat_id} exceeded. Dropping message {parsed['message_id']}")
        if parsed["sender_phone"] == owner_clean:
            alert_msg = "⚠️ *System Alert*: You are sending too many messages. Some messages may be skipped to prevent overload."
            if parsed.get("instance") == "agent-session":
                from app.services.agent_instance_service import agent_instance_service
                await agent_instance_service.send_via_agent(parsed["sender_phone"], alert_msg)
            else:
                await whatsapp_service.send_text(parsed["sender_phone"], alert_msg)
        return {"status": "dropped_queue_full", "message_id": parsed["message_id"]}

    await q.put(parsed)
    return {"status": "queued", "message_id": parsed["message_id"]}


async def _store_agent_qr_from_payload(payload: dict[str, Any]) -> bool:
    """Extract and cache agent QR code data."""
    def _extract_qr(value: Any) -> str:
        if isinstance(value, str):
            return value if ("base64" in value or len(value) > 100) else ""
        if isinstance(value, dict):
            for key in ("base64", "qrcode", "qr", "code"):
                found = _extract_qr(value.get(key))
                if found:
                    return found
            for child in value.values():
                found = _extract_qr(child)
                if found:
                    return found
        return ""
    
    qr_data = _extract_qr(payload.get("data") or payload)
    if not qr_data:
        return False
    from app.services.agent_instance_service import AGENT_QR_CACHE_KEY
    await cache_set(AGENT_QR_CACHE_KEY, qr_data, ttl_seconds=180)
    logger.info("Agent QR code cached (180s TTL)")
    return True


@router.post("/webhook/agent-qr")
async def agent_qr_webhook(request: Request) -> dict[str, str]:
    """Dedicated QR code update webhook for agent."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "bad_json"}
    return {"status": "ok" if await _store_agent_qr_from_payload(payload) else "no_qr"}


@router.post("/webhook/agent")
async def agent_webhook(request: Request) -> dict[str, str]:
    """Main webhook for the agent WhatsApp instance."""
    return await evolution_webhook(request)

