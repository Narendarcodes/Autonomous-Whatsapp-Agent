"""Slice 4: post-admission stage chain tests (ADR-0007).

The pipeline's external collaborators (STT, Hermes dispatch, WhatsApp send)
are faked; everything DB-backed runs against the real test schema.
Closes #11 (dedupe fallback tested in test_inbox_accept) and #12 (upsert race).
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.intake.stages import MessagePipeline, get_or_create_user
from app.intake.types import InboundMessage
from app.models.models import User


def phone() -> str:
    """Unique numeric phone per call — avoids cross-run unique collisions."""
    return f"1555{int(uuid.uuid4().hex[:8], 16) % 10_000_000_000:010d}"


def msg(
    *,
    sender: str,
    text="hello",
    is_group=False,
    chat=None,
    instance="my-session",
    quoted="",
    message_id="MSG-1",
) -> InboundMessage:
    chat = chat or sender
    return InboundMessage(
        sender_phone=sender,
        chat_id=chat,
        is_group=is_group,
        group_id=chat if is_group else "",
        message_text=text,
        message_id=message_id,
        timestamp="1724500000",
        is_audio=False,
        push_name="",
        quoted_text=quoted,
        bot_phone="15550000000",
        instance=instance,
        bot_mode="self_chat",
    )


class Recorder:
    def __init__(self, dispatch_ok=True):
        self.dispatch_ok = dispatch_ok
        self.dispatched: list[tuple[str, str, bool]] = []
        self.replies: list[tuple[str, str]] = []
        self.transcribed_from: list[str] = []

    async def dispatch(self, session_id, final_text, use_agent):
        self.dispatched.append((session_id, final_text, use_agent))
        return self.dispatch_ok

    async def reply(self, message, to, text):
        self.replies.append((to, text))
        return True

    async def transcribe(self, message_id):
        self.transcribed_from.append(message_id)
        return "transcribed words"


async def make_owner(db, wa_phone: str) -> User:
    db.add(User(wa_phone=wa_phone, is_owner=True, has_permission=True, display_name="Owner"))
    await db.commit()
    res = await db.execute(select(User).where(User.wa_phone == wa_phone))
    return res.scalar_one()


# ------------------------------------------------------------------ tests


async def test_dpdp_group_message_without_mention_dropped(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    s = phone()
    await pipeline(msg(sender=s, text="hello everyone", is_group=True, chat="1203999@g.us"))
    assert rec.dispatched == []  # dropped silently before anything else


async def test_dpdp_group_message_with_assistant_mention_dispatches(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    group = "12039988888"
    await make_owner(db_session, owner)
    # legacy quirk preserved: groups are whitelisted by their bare id
    db_session.add(User(wa_phone=group, has_permission=True))
    await db_session.commit()

    m = msg(sender=owner, text="@assistant what's next?", is_group=True, chat=f"{group}@g.us")
    await pipeline(m)

    assert len(rec.dispatched) == 1
    session_id, final_text, use_agent = rec.dispatched[0]
    assert session_id == f"{group}@g.us" and use_agent is False
    assert final_text == "[+{0}]: @assistant what's next?".format(owner)


async def test_owner_dm_dispatches_plain_text(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    m = msg(sender=owner, text="remind me about lunch", chat=owner)
    await pipeline(m)

    assert rec.dispatched == [(owner, "remind me about lunch", False)]
    assert rec.replies == []


async def test_quoted_reply_prefixes_context(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    m = msg(sender=owner, chat=owner, text="explain more", quoted="earlier statement")
    await pipeline(m)

    assert rec.dispatched[0][1] == '[Replying to: "earlier statement"] explain more'


async def test_slash_command_intercepts_before_dispatch(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    m = msg(sender=owner, chat=owner, text=f"/allow {phone()}")
    await pipeline(m)

    assert rec.dispatched == []          # command consumed the message
    assert len(rec.replies) == 1         # parser answered via WhatsApp reply
    assert "Allowed." in rec.replies[0][1]


async def test_untrusted_sender_dropped_even_when_whitelisted(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    sender = phone()
    db_session.add(
        User(wa_phone=sender, has_permission=True, trust_level="untrusted", display_name="Shady")
    )
    await db_session.commit()

    await pipeline(msg(sender=sender, chat=sender))
    assert rec.dispatched == []


async def test_voice_message_transcribed_then_dispatched(db_session):
    rec = Recorder()
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    base = msg(sender=owner, chat=owner, text="[Voice Message]", message_id="AUD-9")
    from dataclasses import replace as dc_replace

    m = dc_replace(base, is_audio=True)
    await pipeline(m)

    assert rec.transcribed_from == ["AUD-9"]
    assert rec.dispatched[0][1] == "transcribed words"


# ------------------------------------------------- brain failure (#8)


async def test_hermes_failure_sends_fallback_reply(db_session):
    rec = Recorder(dispatch_ok=False)
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    m = msg(sender=owner, chat=owner, text="hello")
    await pipeline(m)

    assert len(rec.dispatched) == 1                      # dispatch was attempted
    assert len(rec.replies) == 1                         # fallback reached the sender
    assert "temporarily unavailable" in rec.replies[0][1]


async def test_hermes_success_sends_no_fallback(db_session):
    rec = Recorder(dispatch_ok=True)
    pipeline = MessagePipeline(transcribe=rec.transcribe, dispatch=rec.dispatch, reply=rec.reply)
    owner = phone()
    await make_owner(db_session, owner)

    await pipeline(msg(sender=owner, chat=owner, text="hi"))
    assert rec.replies == []


async def test_upsert_race_creates_exactly_one_row(db_session):
    """#12: two concurrent first-messages for the same new number must not
    crash a worker or duplicate the row."""
    from app.db.database import AsyncSessionLocal

    target = phone()

    async def create():
        async with AsyncSessionLocal() as db:
            user = await get_or_create_user(db, target, display_name="Racer")
            return user.id

    id1, id2 = await asyncio.gather(create(), create())
    assert id1 == id2
    res = await db_session.execute(select(User).where(User.wa_phone == target))
    rows = res.scalars().all()
    assert len(rows) == 1
