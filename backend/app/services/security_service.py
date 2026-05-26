"""Security service — enforces chat and sender ACLs.

Called by the webhook handler BEFORE a message is queued to Redis.
Returns an action string: 'allow', 'silent_log', or 'block'.
"""
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.models import AuditLog, ChatACL, SenderACL, User

logger = get_logger(__name__)


class SecurityService:
    async def get_chat_mode(self, db: AsyncSession, chat_id: str) -> str:
        """Return the effective mode for a chat: 'allow_all', 'silent_log', or 'block'."""
        result = await db.execute(select(ChatACL).where(ChatACL.chat_id == chat_id))
        acl = result.scalar_one_or_none()
        if acl is None:
            return "silent_log"   # default: new chats are silent until opted in
        return acl.mode

    async def get_sender_trust(self, db: AsyncSession, sender_phone: str) -> str:
        """Return trust level: 'vip', 'normal', 'unknown', 'blocked'."""
        result = await db.execute(
            select(SenderACL).where(SenderACL.sender_phone == sender_phone)
        )
        acl = result.scalar_one_or_none()
        if acl is None:
            return "unknown"
        return acl.trust_level

    async def check_quiet_hours(self, user: User) -> bool:
        """Return True if we are currently in quiet hours (agent should be silent)."""
        from app.services.preferences_service import preferences_service
        quiet_start = await preferences_service.get(user.id, "quiet_hours_start")
        quiet_end = await preferences_service.get(user.id, "quiet_hours_end")
        if not quiet_start or not quiet_end:
            return False
        try:
            now_local = datetime.now(timezone.utc).astimezone()
            t_now = now_local.time().replace(second=0, microsecond=0)
            h_s, m_s = map(int, quiet_start.split(":"))
            h_e, m_e = map(int, quiet_end.split(":"))
            t_start = time(h_s, m_s)
            t_end = time(h_e, m_e)
            if t_start <= t_end:
                return t_start <= t_now <= t_end
            else:   # wraps midnight
                return t_now >= t_start or t_now <= t_end
        except Exception:
            return False

    async def evaluate(
        self,
        db: AsyncSession,
        chat_id: str,
        sender_phone: str,
        user: User | None,
    ) -> str:
        """
        Full ACL evaluation. Precedence:
        1. Sender blocked → 'block'
        2. Chat blocked   → 'block'
        3. Quiet hours    → 'silent_log'
        4. Chat mode (allow_all / silent_log)
        """
        sender_trust = await self.get_sender_trust(db, sender_phone)
        if sender_trust == "blocked":
            return "block"

        chat_mode = await self.get_chat_mode(db, chat_id)
        if chat_mode == "block":
            return "block"

        if user and await self.check_quiet_hours(user):
            return "silent_log"

        return chat_mode   # 'allow_all' or 'silent_log'

    async def ensure_owner_allowed(self, db: AsyncSession, owner_phone: str) -> None:
        """On first run, make sure the owner's own DM is in allow_all mode."""
        result = await db.execute(
            select(ChatACL).where(ChatACL.chat_id == owner_phone)
        )
        if result.scalar_one_or_none() is None:
            acl = ChatACL(
                chat_id=owner_phone,
                is_group=False,
                mode="allow_all",
                memory_enabled=True,
                display_name="Owner DM",
            )
            db.add(acl)
            await db.commit()
            logger.info("Seeded owner DM %s as allow_all", owner_phone)

    async def set_chat_mode(
        self,
        db: AsyncSession,
        chat_id: str,
        mode: str,
        is_group: bool = False,
        display_name: str | None = None,
    ) -> ChatACL:
        result = await db.execute(select(ChatACL).where(ChatACL.chat_id == chat_id))
        acl = result.scalar_one_or_none()
        if acl is None:
            acl = ChatACL(chat_id=chat_id, is_group=is_group)
            db.add(acl)
        acl.mode = mode
        if display_name:
            acl.display_name = display_name
        await db.commit()
        await db.refresh(acl)
        return acl

    async def set_sender_trust(
        self,
        db: AsyncSession,
        sender_phone: str,
        trust_level: str,
        display_name: str | None = None,
    ) -> SenderACL:
        result = await db.execute(
            select(SenderACL).where(SenderACL.sender_phone == sender_phone)
        )
        acl = result.scalar_one_or_none()
        if acl is None:
            acl = SenderACL(sender_phone=sender_phone)
            db.add(acl)
        acl.trust_level = trust_level
        if display_name:
            acl.display_name = display_name
        await db.commit()
        await db.refresh(acl)
        return acl


security_service = SecurityService()
