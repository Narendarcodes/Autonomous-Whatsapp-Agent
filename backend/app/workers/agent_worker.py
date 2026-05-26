"""Agent worker — consumes the message_queue Redis Stream and processes messages.

Run with:  python -m app.workers.agent_worker
"""
import asyncio
import signal
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.database import AsyncSessionLocal
from app.db.redis_client import (
    MESSAGE_STREAM,
    MESSAGE_STREAM_DEAD,
    MESSAGE_STREAM_GROUP,
    ensure_consumer_group,
    get_redis,
)
from app.models.models import EventCache, PendingDecision, User
from app.services.agent_engine import agent_engine
from app.services.calendar_service import calendar_service
from app.services.command_parser import handle_command
from app.services.permission_service import permission_service
from app.services.proactive_service import proactive_service
from app.services.whatsapp_service import whatsapp_service

logger = get_logger("agent_worker")

CONSUMER_NAME = "worker-1"
STOP = False


def _handle_stop(*_):
    global STOP
    STOP = True
    logger.info("Stop signal received")


async def _execute_approved_decision(db, decision: PendingDecision) -> str:
    """Carry out an action that the owner just approved."""
    action = decision.action_type
    proposed = decision.proposed_action or {}

    user_result = await db.execute(select(User).where(User.id == decision.user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        return "Approved, but user record vanished — nothing to do."

    if action == "create_event":
        from dateutil import parser as date_parser
        result = await calendar_service.create_event(
            db,
            user,
            summary=proposed["summary"],
            start_time=date_parser.parse(proposed["start_time"]),
            end_time=date_parser.parse(proposed["end_time"]),
            description=proposed.get("description", ""),
            location=proposed.get("location", ""),
            attendees=proposed.get("attendees") or [],
            create_meet_link=bool(proposed.get("create_meet_link", False)),
            source_chat=decision.source_chat,
        )
        if not result:
            return "I tried to create the event but Google rejected it."

        # Schedule reminders + check conflicts
        event_q = await db.execute(
            select(EventCache).where(EventCache.id == result["id"])
        )
        event = event_q.scalar_one()
        await proactive_service.schedule_event_reminders(db, user, event)

        msg = f"Done. Created: {result['summary']}"
        if result.get("meet_link"):
            msg += f"\nGoogle Meet: {result['meet_link']}"
        return msg

    if action == "delete_event":
        ok = await calendar_service.delete_event(db, user, proposed["google_event_id"])
        return "Deleted." if ok else "Couldn't delete it."

    if action == "group_reply":
        ok = await whatsapp_service.send_text(proposed["group_id"], proposed["reply_text"])
        return "Posted." if ok else "Failed to post to the group."

    return f"Approved but I don't know how to execute action '{action}'."


async def _process_one(stream_id: str, fields: dict) -> None:
    user_id = fields.get("user_id", "")
    sender_phone = fields.get("wa_phone", "")
    chat_id = fields.get("chat_id", "")
    is_group = fields.get("is_group", "false") == "true"
    group_id = fields.get("group_id", "")
    message_text = fields.get("message_text", "")

    if not message_text:
        return

    reply_to = group_id if (is_group and group_id) else chat_id or sender_phone

    async with AsyncSessionLocal() as db:
        # Look up user
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            logger.warning("User %s not found", user_id)
            return

        # First: does this message resolve a pending permission decision?
        # Only the owner can resolve.
        is_owner_msg = sender_phone == settings.OWNER_WA_PHONE.lstrip("+") and not is_group
        if is_owner_msg:
            # Check for slash commands before anything else
            async with AsyncSessionLocal() as db:
                cmd_reply = await handle_command(db, user.id, message_text)
                if cmd_reply is not None:
                    await whatsapp_service.send_text(settings.OWNER_WA_PHONE, cmd_reply)
                    return

            decision = await permission_service.try_resolve(db, message_text)
            if decision is not None:
                if decision.status == "approved":
                    result_msg = await _execute_approved_decision(db, decision)
                    await whatsapp_service.send_text(settings.OWNER_WA_PHONE, result_msg)
                elif decision.status == "rejected":
                    await whatsapp_service.send_text(
                        settings.OWNER_WA_PHONE, f"OK, '{decision.action_type}' cancelled."
                    )
                elif decision.status == "expired":
                    await whatsapp_service.send_text(
                        settings.OWNER_WA_PHONE, "That request expired. Send the original message again to retry."
                    )
                return

        # Otherwise: let the agent process it
        try:
            reply = await agent_engine.process_message(
                db=db,
                user=user,
                chat_id=chat_id,
                message_text=message_text,
                is_group=is_group,
                group_id=group_id or None,
            )
        except Exception as exc:
            logger.exception("Agent processing failed: %s", exc)
            reply = "Something went wrong. I'll try again next time."

        if not reply:
            return

        # Permission gate for group replies
        if is_group and await permission_service.is_required("group_reply"):
            await permission_service.request_permission(
                db=db,
                user=user,
                action_type="group_reply",
                proposed_action={
                    "group_id": group_id,
                    "reply_text": reply,
                    "original_message": message_text[:200],
                },
                source_chat=chat_id,
            )
            logger.info("Group reply queued for owner approval")
            return

        await whatsapp_service.send_text(reply_to, reply)


async def _main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    logger.info("Agent worker starting (consumer=%s)", CONSUMER_NAME)
    await ensure_consumer_group()
    r = await get_redis()

    while not STOP:
        try:
            entries = await r.xreadgroup(
                groupname=MESSAGE_STREAM_GROUP,
                consumername=CONSUMER_NAME,
                streams={MESSAGE_STREAM: ">"},
                count=10,
                block=5000,
            )
        except Exception as exc:
            logger.error("XREADGROUP failed: %s", exc)
            await asyncio.sleep(2)
            continue

        if not entries:
            continue

        for _stream, messages in entries:
            for msg_id, fields in messages:
                try:
                    await _process_one(msg_id, fields)
                    await r.xack(MESSAGE_STREAM, MESSAGE_STREAM_GROUP, msg_id)
                except Exception as exc:
                    logger.exception("Failed to process %s: %s", msg_id, exc)
                    # Send to dead-letter stream
                    try:
                        await r.xadd(MESSAGE_STREAM_DEAD, {**fields, "error": str(exc)[:500]})
                        await r.xack(MESSAGE_STREAM, MESSAGE_STREAM_GROUP, msg_id)
                    except Exception:
                        pass

    logger.info("Agent worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
