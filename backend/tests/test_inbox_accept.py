"""Interface tests for the Inbox intake module (ADR-0007).

The interface is the test surface: every test drives inbox.accept() and
asserts on Acks + observable outcomes in the fakes. No Redis, no Postgres,
no HTTP.
"""
import pytest

from app.intake import Ack, InboundMessage, make_inbox


def msg(
    sender="15551234567",
    chat=None,
    text="hello",
    message_id="ABC123",
    is_group=False,
    **overrides,
) -> InboundMessage:
    base = dict(
        sender_phone=sender,
        chat_id=chat or (f"{sender}@g.us" if is_group else f"{sender}@s.whatsapp.net"),
        is_group=is_group,
        group_id=f"{sender}@g.us" if is_group else "",
        message_text=text,
        message_id=message_id,
        timestamp="1724500000",
        is_audio=False,
        push_name="Tester",
        quoted_text="",
        bot_phone="15550000000",
        instance="my-session",
        bot_mode="self_chat",
    )
    base.update(overrides)
    return InboundMessage(**base)


# ---------------------------------------------------------------- accepted


@pytest.mark.asyncio
async def test_first_message_is_accepted_and_enqueued():
    inbox, fakes = make_inbox()
    ack = await inbox.accept(msg())
    assert ack is Ack.ACCEPTED
    assert fakes["stream"].total_pending() == 1


@pytest.mark.asyncio
async def test_per_chat_order_preserved():
    inbox, fakes = make_inbox()
    for i in range(3):
        await inbox.accept(msg(message_id=f"M{i}", text=f"m{i}"))
    q = fakes["stream"].queues["15551234567@s.whatsapp.net"]
    assert [m.message_text for m in q] == ["m0", "m1", "m2"]


# --------------------------------------------------------------- duplicate


@pytest.mark.asyncio
async def test_replayed_message_id_is_duplicate():
    inbox, _ = make_inbox()
    first = await inbox.accept(msg(message_id="X1"))
    replay = await inbox.accept(msg(message_id="X1"))
    assert (first, replay) == (Ack.ACCEPTED, Ack.DUPLICATE)


@pytest.mark.asyncio
async def test_duplicate_does_not_consume_rate_budget():
    """Gate order: idempotency runs BEFORE rate limit — a replay flood must not
    burn the sender's real budget."""
    inbox, _ = make_inbox(rate_limit=1)
    assert await inbox.accept(msg(text="hi")) is Ack.ACCEPTED
    # replays of the SAME message: duplicate wins even though budget is spent
    assert await inbox.accept(msg(text="hi")) is Ack.DUPLICATE
    assert await inbox.accept(msg(text="hi")) is Ack.DUPLICATE


# ------------------------------------------------------------- rate limited


@pytest.mark.asyncio
async def test_flood_hits_rate_limit():
    inbox, _ = make_inbox(rate_limit=2)
    assert await inbox.accept(msg(message_id="A")) is Ack.ACCEPTED
    assert await inbox.accept(msg(message_id="B")) is Ack.ACCEPTED
    assert await inbox.accept(msg(message_id="C")) is Ack.RATE_LIMITED


# --------------------------------------------------------------- loop guard


@pytest.mark.asyncio
async def test_own_send_echo_is_ignored():
    own = {("ECHO1", None)}
    inbox, fakes = make_inbox(own_sends=set())
    from app.intake.gates import text_fingerprint

    fp = text_fingerprint("15551234567@s.whatsapp.net", "echo text")
    fakes["sent_log"].record("ECHO1", fp)

    ack = await inbox.accept(msg(message_id="ECHO1", text="anything"))
    assert ack is Ack.IGNORED

    ack = await inbox.accept(msg(message_id="OTHER", text="echo text"))
    assert ack is Ack.IGNORED  # content hash also matches our send


# -------------------------------------------------------------- queue cap


@pytest.mark.asyncio
async def test_queue_cap_rejects_when_chat_backed_up():
    inbox, fakes = make_inbox(max_pending_per_chat=2)
    assert await inbox.accept(msg(message_id="A")) is Ack.ACCEPTED
    assert await inbox.accept(msg(message_id="B")) is Ack.ACCEPTED
    assert await inbox.accept(msg(message_id="C")) is Ack.REJECTED_QUEUE_FULL
    assert fakes["stream"].total_pending() == 2  # nothing new admitted


# ------------------------------------------------------- idempotency (#11)


@pytest.mark.asyncio
async def test_events_without_message_id_still_dedupe_by_content():
    """#11: legacy path SKIPPED idempotency when the event had no message id.
    The seam now falls back to a content fingerprint."""
    inbox, fakes = make_inbox()
    first = await inbox.accept(msg(message_id="", text="no id here", timestamp="1724500000"))
    replay = await inbox.accept(msg(message_id="", text="no id here", timestamp="1724500000"))
    assert (first, replay) == (Ack.ACCEPTED, Ack.DUPLICATE)
    assert fakes["stream"].total_pending() == 1


@pytest.mark.asyncio
async def test_different_content_without_ids_not_conflated():
    inbox, _ = make_inbox()
    a = await inbox.accept(msg(message_id="", text="first", timestamp="t1"))
    b = await inbox.accept(msg(message_id="", text="second", timestamp="t2"))
    assert (a, b) == (Ack.ACCEPTED, Ack.ACCEPTED)


# ------------------------------------------------------------ independence


@pytest.mark.asyncio
async def test_chats_are_independent_for_caps():
    inbox, fakes = make_inbox(max_pending_per_chat=1)
    a = "15551111111@s.whatsapp.net"
    b = "15552222222@s.whatsapp.net"
    assert await inbox.accept(msg(sender=a[:11], message_id="A1")) is Ack.ACCEPTED
    assert await inbox.accept(msg(sender=b[:11], message_id="B1")) is Ack.ACCEPTED
    assert await inbox.accept(msg(sender=a[:11], message_id="A2")) is Ack.REJECTED_QUEUE_FULL
    assert await fakes["stream"].depth(a) == 1
