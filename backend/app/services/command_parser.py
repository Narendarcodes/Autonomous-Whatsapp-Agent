"""DM command parser — intercepts slash-commands from the owner.

These commands mutate chat_acl, sender_acl, or user_preferences
directly without going through the LLM. Called first in
agent_worker before any LLM routing.

Supported commands:
    /allow  <phone|group_jid>         → set chat mode to allow_all
    /block  <phone|group_jid>         → set chat mode to block
    /trust  <phone>                   → set sender trust to vip
    /untrust <phone>                  → set sender trust to normal
    /silence <phone|group_jid>        → set chat mode to silent_log
    /quiet  <HH:MM>-<HH:MM>          → set quiet hours (e.g. /quiet 22:00-07:00)
    /memory-on  <phone|group_jid>     → enable memory for a chat
    /memory-off <phone|group_jid>     → disable memory for a chat
    /voice-on  <phone|group_jid>      → enable voice transcription for a chat
    /voice-off <phone|group_jid>      → disable voice transcription for a chat
    /set <key> <value>                → set a preference key/value
    /show prefs                       → dump current preferences
    /show audit [N]                   → last N audit log entries (default 10)
    /show acl                         → dump all chat_acl rows
"""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.models import AuditLog, ChatACL, SenderACL
from app.services.preferences_service import preferences_service
from app.services.security_service import security_service

logger = get_logger(__name__)

COMMAND_RE = re.compile(r"^/(\S+)(.*)$", re.IGNORECASE)


async def handle_command(
    db: AsyncSession,
    user_id: str,
    text: str,
) -> str | None:
    """
    If `text` is a slash-command, execute it and return a reply string.
    Return None if the text is not a command (let it go to the LLM).
    """
    m = COMMAND_RE.match(text.strip())
    if not m:
        return None

    cmd = m.group(1).lower()
    args = m.group(2).strip()

    try:
        if cmd == "allow":
            return await _cmd_allow(db, user_id, args)
        if cmd == "block":
            return await _cmd_block(db, user_id, args)
        if cmd == "silence":
            return await _cmd_silence(db, user_id, args)
        if cmd == "trust":
            return await _cmd_trust(db, user_id, args, "vip")
        if cmd == "untrust":
            return await _cmd_trust(db, user_id, args, "normal")
        if cmd == "quiet":
            return await _cmd_quiet(db, user_id, args)
        if cmd in ("memory-on", "memory_on"):
            return await _cmd_memory(db, user_id, args, enabled=True)
        if cmd in ("memory-off", "memory_off"):
            return await _cmd_memory(db, user_id, args, enabled=False)
        if cmd in ("voice-on", "voice_on"):
            return await _cmd_voice(db, user_id, args, enabled=True)
        if cmd in ("voice-off", "voice_off"):
            return await _cmd_voice(db, user_id, args, enabled=False)
        if cmd == "set":
            return await _cmd_set(db, user_id, args)
        if cmd == "show":
            return await _cmd_show(db, user_id, args)
    except Exception as exc:
        logger.exception("Command /%s failed: %s", cmd, exc)
        return f"Command error: {exc}"

    return None   # unknown command — let LLM handle it


# ==================== COMMAND HANDLERS ====================

async def _cmd_allow(db: AsyncSession, user_id: str, args: str) -> str:
    target = args.strip()
    if not target:
        return "Usage: /allow <phone or group JID>"
    is_group = "@g.us" in target
    await security_service.set_chat_mode(db, target, "allow_all", is_group=is_group)
    _audit(db, user_id, "acl.allow", {"target": target})
    return f"Allowed. Messages from {target} will now be processed."


async def _cmd_block(db: AsyncSession, user_id: str, args: str) -> str:
    target = args.strip()
    if not target:
        return "Usage: /block <phone or group JID>"
    is_group = "@g.us" in target
    await security_service.set_chat_mode(db, target, "block", is_group=is_group)
    _audit(db, user_id, "acl.block", {"target": target})
    return f"Blocked. Messages from {target} will be silently dropped."


async def _cmd_silence(db: AsyncSession, user_id: str, args: str) -> str:
    target = args.strip()
    if not target:
        return "Usage: /silence <phone or group JID>"
    is_group = "@g.us" in target
    await security_service.set_chat_mode(db, target, "silent_log", is_group=is_group)
    _audit(db, user_id, "acl.silence", {"target": target})
    return f"Silenced. Messages from {target} will be logged but not acted on."


async def _cmd_trust(db: AsyncSession, user_id: str, args: str, level: str) -> str:
    phone = args.strip().lstrip("+")
    if not phone:
        return f"Usage: /trust <phone>"
    await security_service.set_sender_trust(db, phone, level)
    _audit(db, user_id, f"acl.trust.{level}", {"phone": phone})
    return f"Trust level for {phone} set to {level}."


async def _cmd_quiet(db: AsyncSession, user_id: str, args: str) -> str:
    # expects "22:00-07:00"
    pattern = re.match(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", args.strip())
    if not pattern:
        return "Usage: /quiet HH:MM-HH:MM  (e.g. /quiet 22:00-07:00)"
    start, end = pattern.group(1), pattern.group(2)
    await preferences_service.set(user_id, "quiet_hours_start", start)
    await preferences_service.set(user_id, "quiet_hours_end", end)
    _audit(db, user_id, "prefs.quiet_hours", {"start": start, "end": end})
    return f"Quiet hours set: {start} → {end}. Agent will be silent during this window."


async def _cmd_memory(db: AsyncSession, user_id: str, args: str, enabled: bool) -> str:
    target = args.strip()
    if not target:
        return f"Usage: /{'memory-on' if enabled else 'memory-off'} <phone or group JID>"
    result = await db.execute(select(ChatACL).where(ChatACL.chat_id == target))
    acl = result.scalar_one_or_none()
    if acl is None:
        return f"Chat {target} not in ACL yet. /allow it first."
    acl.memory_enabled = enabled
    await db.commit()
    state = "enabled" if enabled else "disabled"
    return f"Memory {state} for {target}."


async def _cmd_voice(db: AsyncSession, user_id: str, args: str, enabled: bool) -> str:
    target = args.strip()
    if not target:
        return f"Usage: /{'voice-on' if enabled else 'voice-off'} <phone or group JID>"
    result = await db.execute(select(ChatACL).where(ChatACL.chat_id == target))
    acl = result.scalar_one_or_none()
    if acl is None:
        return f"Chat {target} not in ACL yet. /allow it first."
    acl.voice_enabled = enabled
    await db.commit()
    state = "enabled" if enabled else "disabled"
    return f"Voice transcription {state} for {target}."


async def _cmd_set(db: AsyncSession, user_id: str, args: str) -> str:
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return "Usage: /set <key> <value>"
    key, value = parts[0], parts[1]
    await preferences_service.set(user_id, key, value, source="explicit")
    return f"Preference saved: {key} = {value}"


async def _cmd_show(db: AsyncSession, user_id: str, args: str) -> str:
    sub = args.strip().lower()

    if sub == "prefs":
        prefs = await preferences_service.get_all(user_id)
        if not prefs:
            return "No preferences set yet."
        lines = [f"  {k} = {v}" for k, v in sorted(prefs.items())]
        return "Current preferences:\n" + "\n".join(lines)

    if sub.startswith("audit"):
        parts = sub.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        result = await db.execute(
            select(AuditLog)
            .where(AuditLog.user_id == user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(n)
        )
        logs = result.scalars().all()
        if not logs:
            return "No audit entries."
        lines = [f"  [{l.created_at.strftime('%m-%d %H:%M')}] {l.action}" for l in logs]
        return f"Last {len(logs)} actions:\n" + "\n".join(lines)

    if sub == "acl":
        result = await db.execute(select(ChatACL).order_by(ChatACL.updated_at.desc()))
        rows = result.scalars().all()
        if not rows:
            return "ACL is empty."
        lines = [
            f"  {r.chat_id[:30]:<30} {r.mode:<12} mem={r.memory_enabled}"
            for r in rows
        ]
        return "Chat ACL:\n" + "\n".join(lines)

    return "Usage: /show prefs | /show audit [N] | /show acl"


def _audit(db: AsyncSession, user_id: str, action: str, details: dict) -> None:
    db.add(AuditLog(user_id=user_id, action=action, details=details))
