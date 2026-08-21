"""Permission/confirmation flow.

When the agent wants to take a sensitive action (create event, delete event,
reply to a group), it can create a PendingDecision and ask the owner via DM:

    "I want to schedule 'Team sync' tomorrow 7-8pm. Reply A1B2 yes or A1B2 no."

The owner replies with the short code, and the agent_worker resolves the
decision.
"""
import json
import random
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.models import PendingDecision, User
from app.services.whatsapp_service import whatsapp_service

logger = get_logger(__name__)


def _generate_short_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


def _format_permission_message(action_type: str, proposed: dict, code: str) -> str:
    if action_type == "create_event":
        start = proposed.get("start_time", "?")
        end = proposed.get("end_time", "?")
        summary = proposed.get("summary", "(no title)")
        location = proposed.get("location") or ""
        loc = f"\nLocation: {location}" if location else ""
        return (
            f"[Approval needed]\n"
            f"Create event: {summary}\n"
            f"When: {start} → {end}{loc}\n\n"
            f"Reply '{code} yes' to confirm or '{code} no' to reject."
        )
    if action_type == "delete_event":
        summary = proposed.get("summary", "(unknown)")
        return (
            f"[Approval needed]\n"
            f"Delete event: {summary}\n\n"
            f"Reply '{code} yes' to delete or '{code} no' to keep."
        )
    if action_type == "group_reply":
        group_name = proposed.get("group_name") or proposed.get("group_id", "a group")
        preview = (proposed.get("reply_text") or "")[:200]
        return (
            f"[Approval needed]\n"
            f"Reply in {group_name}:\n"
            f'"{preview}"\n\n'
            f"Send '{code} yes' to post or '{code} no' to cancel."
        )
    return (
        f"[Approval needed]\n"
        f"Action: {action_type}\n"
        f"Details: {json.dumps(proposed)[:300]}\n\n"
        f"Reply '{code} yes' or '{code} no'."
    )


class PermissionService:
    async def is_required(self, action_type: str) -> bool:
        mapping = {
            "create_event": settings.PERMISSION_REQUIRED_FOR_CREATE,
            "delete_event": settings.PERMISSION_REQUIRED_FOR_DELETE,
            "group_reply": settings.PERMISSION_REQUIRED_FOR_GROUP_REPLY,
        }
        return mapping.get(action_type, False)

    async def request_permission(
        self,
        db: AsyncSession,
        user: User,
        action_type: str,
        proposed_action: dict,
        source_chat: str | None = None,
    ) -> PendingDecision:
        """Create a pending decision and DM the owner asking for approval."""
        code = _generate_short_code()
        # Ensure uniqueness
        for _ in range(5):
            result = await db.execute(select(PendingDecision).where(PendingDecision.short_code == code))
            if result.scalar_one_or_none() is None:
                break
            code = _generate_short_code()

        expires = datetime.now(timezone.utc) + timedelta(minutes=settings.PERMISSION_TIMEOUT_MINUTES)
        decision = PendingDecision(
            user_id=user.id,
            short_code=code,
            action_type=action_type,
            proposed_action=proposed_action,
            source_chat=source_chat,
            status="awaiting",
            expires_at=expires,
        )
        db.add(decision)
        await db.commit()
        await db.refresh(decision)

        # Send DM to the OWNER (always — even if trigger came from another chat)
        from app.services.preferences_service import preferences_service
        bot_phone = await preferences_service.get_owner_preference("bot_phone")
        owner_target = (bot_phone or settings.OWNER_WA_PHONE).lstrip("+")
        
        msg = _format_permission_message(action_type, proposed_action, code)
        await whatsapp_service.send_text(owner_target, msg)

        logger.info("Created pending decision %s (%s) for %s", code, action_type, user.id)
        return decision

    async def try_resolve(
        self, db: AsyncSession, message_text: str
    ) -> PendingDecision | None:
        """If the message matches a pending short code, mark it resolved.

        Accepts patterns like:
            "A1B2 yes"
            "A1B2 no"
            "a1b2 approve"
        """
        text = message_text.strip().upper()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return None
        code, verdict = parts[0], parts[1].strip().lower()

        if len(code) != 4 or not code.isalnum():
            return None

        result = await db.execute(
            select(PendingDecision).where(PendingDecision.short_code == code)
        )
        decision = result.scalar_one_or_none()
        if not decision or decision.status != "awaiting":
            return None

        if decision.expires_at < datetime.now(timezone.utc):
            decision.status = "expired"
            decision.resolved_at = datetime.now(timezone.utc)
            await db.commit()
            return decision

        if verdict in ("yes", "approve", "ok", "y"):
            decision.status = "approved"
        elif verdict in ("no", "reject", "cancel", "n"):
            decision.status = "rejected"
        else:
            return None

        decision.resolved_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(decision)
        return decision

    async def cleanup_expired(self, db: AsyncSession) -> int:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(PendingDecision).where(
                PendingDecision.status == "awaiting",
                PendingDecision.expires_at < now,
            )
        )
        expired = list(result.scalars().all())
        for d in expired:
            d.status = "expired"
            d.resolved_at = now
        if expired:
            await db.commit()
        return len(expired)

    # ------------------------------------------------------------------
    # Message-level permission cascade (multi-tenant moat)
    # ------------------------------------------------------------------

    async def decide(
        self,
        db: AsyncSession,
        sender_phone: str,
        message_text: str,
        tenant_id: int | None = None,
        is_group: bool = False,
    ) -> dict:
        """Decide what happens to an inbound WhatsApp message.

        Returns {"action": "run"|"hold"|"deny", "needs_owner_approval": bool,
                 "decision": PendingDecision | None, "user": User}

        - Owner → run instantly.
        - Authorized user (has_permission) → run.
        - Stranger (no permission) → hold: create PendingDecision, notify owner.
        """
        phone = (sender_phone or "").lstrip("+")

        result = await db.execute(select(User).where(User.wa_phone == phone))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(wa_phone=phone, tenant_id=tenant_id, has_permission=False)
            db.add(user)
            await db.commit()
            await db.refresh(user)

        if user.is_owner or user.has_permission:
            return {"action": "run", "needs_owner_approval": False, "decision": None, "user": user}

        # Stranger → hold + ask the owner
        decision = await self.request_permission(
            db,
            user,
            action_type="message_task",
            proposed_action={"text": (message_text or "")[:300], "is_group": is_group},
            source_chat=phone,
        )
        return {"action": "hold", "needs_owner_approval": True, "decision": decision, "user": user}


permission_service = PermissionService()
