"""Pure unit tests for the Evolution edge adapter (app.intake.evolution).

No Redis / Postgres / HTTP — IO facts are passed in as arguments.
Behaviour mirrors the legacy webhooks._parse_evolution_event verbatim.
"""
from app.intake.evolution import normalize_event


def payload(
    *,
    event="messages.upsert",
    remote_jid="15551234567@s.whatsapp.net",
    from_me=False,
    text="hello bot",
    message_id="MSG-1",
    instance="my-session",
    extra_msg=None,
    participant=None,
    push_name="Tester",
):
    data = {
        "key": {"remoteJid": remote_jid, "fromMe": from_me, "id": message_id},
        "messageTimestamp": 1724500000,
        "pushName": push_name,
    }
    if text is not None or extra_msg:
        m = {"conversation": text} if text is not None else {}
        if extra_msg:
            m.update(extra_msg)
        data["message"] = m
    if participant:
        data["participant"] = participant
    return {"event": event, "data": data, "instance": instance}


# ------------------------------------------------------------- happy paths


def test_dm_text_message_normalizes():
    out = normalize_event(payload(), bot_phone="15550000000", bot_mode="self_chat")
    assert out is not None
    assert out.sender_phone == "15551234567"
    assert out.chat_id == "15551234567@s.whatsapp.net"
    assert out.message_text == "hello bot"
    assert not out.is_group
    assert out.instance == "my-session"


def test_extended_text_message_body_extracted():
    p = payload(text=None, extra_msg={"extendedTextMessage": {"text": "hi there"}})
    out = normalize_event(p, bot_phone="x", bot_mode="self_chat")
    assert out.message_text == "hi there"


def test_voice_message_gets_placeholder():
    p = payload(text=None, extra_msg={"audioMessage": {"url": "https://x"}})
    out = normalize_event(p, bot_phone="x", bot_mode="self_chat")
    assert out.is_audio and out.message_text == "[Voice Message]"


def test_quoted_reply_context_extracted():
    p = payload(
        text=None,
        extra_msg={
            "extendedTextMessage": {
                "text": "what did you mean?",
                "contextInfo": {
                    "quotedMessage": {"conversation": "earlier statement"},
                },
            }
        },
    )
    out = normalize_event(p, bot_phone="x", bot_mode="self_chat")
    assert out.quoted_text == "earlier statement"


def test_group_message_sender_is_participant():
    p = payload(remote_jid="12039@g.us", participant="15559998888@s.whatsapp.net")
    out = normalize_event(p, bot_phone="x", bot_mode="self_chat")
    assert out.is_group
    assert out.sender_phone == "15559998888"
    assert out.chat_id == "12039@g.us" and out.group_id == "12039@g.us"


# ------------------------------------------------------------ drop reasons


def test_foreign_event_type_dropped():
    assert normalize_event(payload(event="connection.update"), bot_phone="x", bot_mode="self_chat") is None


def test_missing_jid_dropped():
    p = payload()
    p["data"]["key"]["remoteJid"] = ""
    assert normalize_event(p, bot_phone="x", bot_mode="self_chat") is None


def test_outbound_echo_dropped_unless_self_chat():
    p = payload(from_me=True)
    assert normalize_event(p, bot_phone="15551234567", bot_mode="dual_number") is None
    # same event, self_chat mode, bot number matches -> kept
    out = normalize_event(p, bot_phone="15551234567", bot_mode="self_chat")
    assert out is not None


def test_send_message_event_forces_from_me():
    p = payload(event="send.message", from_me=False)
    assert normalize_event(p, bot_phone="other", bot_mode="self_chat") is None


def test_empty_content_without_audio_dropped():
    p = payload(text="")
    assert normalize_event(p, bot_phone="x", bot_mode="self_chat") is None


def test_bot_phone_falls_back_to_payload_sender():
    p = payload()
    p["sender"] = "15550009999@s.whatsapp.net"
    out = normalize_event(p, bot_phone=None, bot_mode="self_chat")
    assert out.bot_phone == "15550009999"
