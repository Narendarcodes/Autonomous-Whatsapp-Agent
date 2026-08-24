"""Post-admission stage chain — the Inbox consumer's handler (ADR-0007 slice 4).

Faithful port of the legacy `_chat_worker` gate sequence. Stage order is
load-bearing and frozen:

    voice STT -> DPDP group filter -> user upsert -> monitored-chat ->
    trusted-sender -> approval intercept -> slash commands -> setup intercept
    -> ACL / quiet hours -> prompt assembly -> Hermes dispatch

Business-rule drops are SILENT successes (no exception); unexpected errors
propagate so the consumer's retry/dead-letter machinery sees them.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.intake.types import InboundMessage
from app.models.models import User

logger = logging.getLogger(__name__)

ReplyFn = Callable[[InboundMessage, str, str], Awaitable[bool]]
DispatchFn = Callable[[str, str, bool], Awaitable[bool]]
TranscribeFn = Callable[[str], Awaitable[str | None]]

FALLBACK_REPLY = (
    "⚠️ I'm temporarily unavailable right now — please try again in a minute."
)


# ------------------------------------------------------------------ user


async def get_or_create_user(
    db: AsyncSession,
    wa_phone: str,
    display_name: str | None = None,
) -> User:
    """Atomic-enough get-or-create (#12): users.wa_phone is UNIQUE, so a lost
    race raises IntegrityError which we absorb by re-selecting the winner's row.
    """
    result = await db.execute(select(User).where(User.wa_phone == wa_phone))
    user = result.scalar_one_or_none()
    if user:
        if display_name and (not user.display_name or user.display_name == f"User {user.wa_phone[-4:]}"):
            user.display_name = display_name
            await db.commit()
            await db.refresh(user)
        return user

    owner_result = await db.execute(select(User).where(User.is_owner == True))  # noqa: E712 - parity with legacy
    has_owner = owner_result.scalar_one_or_none() is not None
    is_owner = not has_owner and wa_phone == settings.OWNER_WA_PHONE.lstrip("+")

    user = User(
        wa_phone=wa_phone,
        is_owner=is_owner,
        timezone=settings.TIMEZONE,
        display_name=display_name,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # concurrent creator won the unique race — adopt their row
        await db.rollback()
        result = await db.execute(select(User).where(User.wa_phone == wa_phone))
        user = result.scalar_one_or_none()
        if user is None:
            raise
        logger.info("User %s created concurrently elsewhere; adopted existing row", wa_phone)
        return user
    await db.refresh(user)
    logger.info("Created user %s (owner=%s, display_name=%s)", wa_phone, is_owner, display_name)
    return user


# -------------------------------------------------------------- pipeline


def _clean_chat(chat_id: str) -> str:
    return chat_id.split("@")[0].split(":")[0].lstrip("+")


class MessagePipeline:
    """Callable stage chain: handler = MessagePipeline(...)."""

    def __init__(
        self,
        *,
        transcribe: TranscribeFn | None = None,
        dispatch: DispatchFn | None = None,
        reply: ReplyFn | None = None,
    ) -> None:
        self._transcribe = transcribe or self._default_transcribe
        self._dispatch = dispatch or self._default_dispatch
        self._reply = reply or self._default_reply

    # -- injected-by-default collaborators -------------------------------

    @staticmethod
    async def _default_transcribe(message_id: str) -> str | None:
        from app.services.whatsapp_service import whatsapp_service

        base64_audio = await whatsapp_service.download_media(message_id)
        if not base64_audio:
            return None
        from app.services.audio_service import audio_service

        return await audio_service.transcribe_audio(base64_audio)

    @staticmethod
    async def _default_dispatch(session_id: str, final_text: str, use_agent: bool) -> bool:
        from app.services.agent_harness import dispatch_to_hermes

        data = await dispatch_to_hermes(session_id, final_text, system_prompt=None, use_agent=use_agent)
        return data is not None

    @staticmethod
    async def _default_reply(message: InboundMessage, to: str, text: str) -> bool:
        from app.outbound import get_outbound

        result = await get_outbound().send(to, text, session_hint=message.instance)
        return bool(result)

    # -- the chain --------------------------------------------------------

    async def __call__(self, message: InboundMessage) -> None:
        text = message.message_text

        # 1. Voice STT
        if message.is_audio:
            transcription = await self._transcribe(message.message_id)
            if transcription:
                text = transcription
                logger.info("Voice message %s transcribed", message.message_id)

        # 2. DPDP privacy filter: groups need explicit mention
        if message.is_group:
            body_lower = text.lower()
            bot_name = getattr(settings, "BOT_NAME", "assistant").lower()
            if "@agent" not in body_lower and bot_name not in body_lower:
                logger.info("Dropped group message %s: no explicit mention", message.message_id)
                return

        # 3..9 everything that needs a DB session
        async with AsyncSessionLocal() as db:
            user = await get_or_create_user(db, message.sender_phone, message.push_name)

            bot_phone = message.bot_phone
            if not bot_phone:
                from app.db.redis_client import cache_get
                from app.services.whatsapp_service import whatsapp_service

                bot_phone = await cache_get("whatsapp:bot_phone") or await whatsapp_service.get_bot_phone()

            from app.services.preferences_service import preferences_service

            owner_bot_phone = await preferences_service.get_owner_preference("bot_phone")
            if owner_bot_phone:
                owner_bot_phone = owner_bot_phone.lstrip("+")

            is_owner = user.is_owner
            if message.bot_mode != "dual_number" and owner_bot_phone and user.wa_phone == owner_bot_phone:
                is_owner = True

            # A. monitored chats: owner DMs always; otherwise chat must be whitelisted
            chat_config: User | None = None
            chat_monitored = False
            if is_owner and not message.is_group:
                chat_monitored = True
            else:
                result = await db.execute(select(User).where(User.wa_phone == _clean_chat(message.chat_id)))
                chat_config = result.scalar_one_or_none()
                chat_monitored = bool(chat_config and chat_config.has_permission)
            if not chat_monitored:
                logger.info("Dropped message %s: chat %s not monitored", message.message_id, message.chat_id)
                return

            # B. trusted senders only (owners bypass)
            if not is_owner and user.trust_level == "untrusted":
                logger.info("Dropped message %s: sender %s untrusted", message.message_id, message.sender_phone)
                return

            # C. owner approval short-codes
            if is_owner:
                from app.services.permission_service import permission_service

                resolved = await permission_service.try_resolve(db, text)
                if resolved:
                    await self._reply(
                        message,
                        message.sender_phone,
                        f"✅ Decision {resolved.short_code} has been {resolved.status}.",
                    )
                    if resolved.status == "approved" and resolved.source_chat:
                        from app.services.whatsapp_service import whatsapp_service

                        notify = (
                            "🔔 The owner has approved the requested action: "
                            f"{resolved.action_type.replace('_', ' ').title()}."
                        )
                        await whatsapp_service.send_text(resolved.source_chat, notify)
                    return

            # D. owner slash commands
            if is_owner and text.strip().startswith("/"):
                from app.services.command_parser import handle_command

                response = await handle_command(db, user.id, text)
                await db.commit()  # audit logs
                if response:
                    await self._reply(message, message.sender_phone, response)
                return

            # E. setup flow intercept
            if is_owner and text.strip().upper() in ("SETUP", "OAUTH", "STATUS"):
                from app.services.setup_service import handle_setup_command

                response = await handle_setup_command(db, user, text.strip().upper())
                if response:
                    await self._reply(message, message.sender_phone, response)
                return

            # F. ACL rules + quiet hours (owners bypass)
            if not is_owner:
                from app.services.security_service import security_service

                acl_action = await security_service.evaluate(db, message.chat_id, message.sender_phone, user)
                if acl_action == "block":
                    logger.info("Ignored %s: blocked by ACL", message.message_id)
                    return
                if acl_action == "silent_log":
                    chat_has_permission = chat_config.has_permission if chat_config else False
                    if not chat_has_permission and not user.has_permission:
                        logger.info("Silently logged %s (quiet hours / silence rule)", message.message_id)
                        return

        # 10. prompt assembly
        if message.quoted_text:
            final_text = f'[Replying to: "{message.quoted_text}"] {text}'
        else:
            final_text = text
        if message.is_group:
            sender_info = (
                f"{message.push_name} (+{message.sender_phone})"
                if message.push_name
                else f"+{message.sender_phone}"
            )
            final_text = f"[{sender_info}]: {final_text}"

        # 11. dispatch — with user-visible failure path (#8)
        use_agent = message.instance == "agent-session"
        logger.info("Dispatching %s to Hermes (session=%s)", message.message_id, message.chat_id)
        delivered_to_brain = await self._dispatch(message.chat_id, final_text, use_agent)
        if not delivered_to_brain:
            logger.warning("Hermes dispatch failed for %s — sending fallback reply", message.message_id)
            await self._reply(message, message.chat_id, FALLBACK_REPLY)


def content_fallback_key(chat_id: str, message_text: str, timestamp: str) -> str:
    """Content-hash idempotency key for events WITHOUT a provider id (#11)."""
    fp = hashlib.md5(f"no-id|{chat_id}|{message_text.strip()}|{timestamp}".encode()).hexdigest()
    return f"no-id:{fp}"
