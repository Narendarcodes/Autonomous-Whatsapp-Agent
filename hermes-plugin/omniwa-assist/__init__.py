"""omniwa-assist — Hermes gateway plugin (volume-deployed, survives recreate).

Two jobs hooked into `pre_gateway_dispatch` (fires for every inbound
MessageEvent before auth/dispatch):

1. CONTACTS INGEST — every group sender identity is pushed (fire-and-forget
   thread, zero latency cost) to the omniWA backend's
   POST /api/contacts/ingest so the dashboard can search people and
   allowlist them with one click.

2. ASSIST KEYWORD GATE (groups only) — instead of paying LLM tokens for all
   group chatter:
     - @mentions / replies-to-bot  -> pass through untouched (direct commands)
     - capability keywords         -> rewritten into ASSIST SUGGESTION MODE:
                                      the agent OFFERS help ("shall I?") but
                                      executes nothing until asked directly
     - everything else             -> {"action": "skip"} — no API call

Configuration via hermes container env:
  WHATSAPP_ASSIST_KEYWORDS   csv of trigger words (has sane defaults)
  OMNIWA_BACKEND_URL         default http://backend:8000
  CONTACT_INGEST_TOKEN       must match backend settings.CONTACT_INGEST_TOKEN
"""

import json
import os
import re
import threading
import time
import urllib.request

_PLUGIN_NAME = "omniwa-assist"

_DEFAULT_KEYWORDS = (
    "book,remind,schedule,weather,order,recommend,plan,appointment,"
    "can anyone,anyone knows,shall we,who can,set up,organise,organize"
)

SUGGEST_DIRECTIVE = (
    "[ASSIST SUGGESTION MODE - HARD CONSTRAINT]\n"
    "A group member said the message below. They did NOT address you directly "
    "and have not tasked you with anything. If one of your capabilities could "
    "genuinely help with it, reply with a SHORT offer describing exactly what "
    "you could do, and end by asking whether you should proceed (e.g. 'shall "
    "I?'). Do NOT execute any tool or take any real action yet. If nothing you "
    "can do fits, stay silent about capabilities and answer casually.\n"
)


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _keywords():
    raw = _env("WHATSAPP_ASSIST_KEYWORDS", _DEFAULT_KEYWORDS)
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


# After the bot OFFERS help in a group, the group's next plain reply ("yes",
# "go ahead") carries no keyword and no @mention, so the gate used to skip it
# and the offer could never be accepted. Remember open offers per chat for a
# short window and let follow-ups through.
_FOLLOWUP_WINDOW_S = 300
_open_offers: dict = {}          # chat_id -> (sender_id, expires_at)
_offers_lock = threading.Lock()


def _norm_id(value) -> str:
    """Bare-compare WhatsApp ids: drop device suffix (``123:7@lid`` == ``123@lid``)."""
    return re.sub(r":\d+(?=@)", "", str(value or "").strip())


def _sender_identity(source) -> dict:
    """Split the gateway's sender id into phone / lid for the backend."""
    raw = _norm_id(getattr(source, "user_id", ""))
    if raw.endswith("@lid"):
        return {"phone": raw, "lid": raw}
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return {"phone": raw.lstrip("+"), "lid": None}


def _remember_offer(chat_id: str, sender_id: str) -> None:
    with _offers_lock:
        _open_offers[chat_id] = (sender_id, time.monotonic() + _FOLLOWUP_WINDOW_S)


def _consume_offer(chat_id: str, sender_id: str) -> bool:
    """True (once) when this sender has an unexpired offer open in this chat."""
    now = time.monotonic()
    with _offers_lock:
        for cid in [c for c, (_, exp) in _open_offers.items() if exp < now]:
            _open_offers.pop(cid, None)
        entry = _open_offers.get(chat_id)
        if entry and entry[0] == sender_id:
            _open_offers.pop(chat_id, None)
            return True
    return False


def _ingest_configured() -> bool:
    return bool(_env("CONTACT_INGEST_TOKEN")) and bool(_env("OMNIWA_BACKEND_URL", "http://backend:8000") or "")


def _post_ingest(source, event) -> None:
    """Fire-and-forget identity push to the omniWA backend."""
    base = _env("OMNIWA_BACKEND_URL", "http://backend:8000").rstrip("/")
    ident = _sender_identity(source)
    if len(ident["phone"]) < 5:
        return
    payload = {
        "contacts": [
            {
                "phone": ident["phone"],
                "lid": ident["lid"],
                "name": getattr(source, "user_name", None),
                "chat_jid": str(getattr(source, "chat_id", "") or ""),
            }
        ]
    }
    req = urllib.request.Request(
        f"{base}/api/contacts/ingest",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Token": _env("CONTACT_INGEST_TOKEN"),
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass  # ingest must never break message flow


def _spawn_ingest(source, event) -> None:
    if not _ingest_configured():
        return
    threading.Thread(target=_post_ingest, args=(source, event), daemon=True).start()


def _is_whatsapp_group(event) -> bool:
    source = getattr(event, "source", None)
    if source is None:
        return False
    platform = str(getattr(source, "platform", "") or "")
    if "whatsapp" not in platform.lower():
        return False
    return getattr(source, "chat_type", "") == "group"


def _mentions_bot(event) -> bool:
    """True when the message tags the bot, replies to a bot message, or is a
    slash command — the same "addressed to me" rules the WhatsApp adapter uses."""
    raw = getattr(event, "raw_message", None) or {}
    if not isinstance(raw, dict):
        raw = {}
    text = str(getattr(event, "text", "") or raw.get("body") or "")
    if text.strip().startswith("/"):
        return True
    bot_ids = {_norm_id(b) for b in (raw.get("botIds") or []) if b}
    if not bot_ids:
        return False
    if {_norm_id(m) for m in (raw.get("mentionedIds") or [])} & bot_ids:
        return True
    quoted_participant = _norm_id(raw.get("quotedParticipant"))
    if quoted_participant and quoted_participant in bot_ids:
        return True
    low = text.lower()
    return any(bare and f"@{bare}" in low for bare in (b.split("@", 1)[0].lower() for b in bot_ids))


def _keyword_hit(text: str) -> str | None:
    """Whole-word / whole-phrase match. Plain substring matching fired on
    "facebook"/"notebook" (book), "border" (order), "explanation"/"planet"
    (plan), burning LLM calls on ordinary chatter."""
    low = (text or "").lower()
    for kw in _keywords():
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low):
            return kw
    return None


def register(ctx):
    """Called by Hermes PluginManager at discovery time."""

    def _pre_gateway_dispatch(event=None, gateway=None, session_store=None, **kwargs):
        if event is None or not _is_whatsapp_group(event):
            return None

        # 1) identity capture for the dashboard directory
        if _ingest_configured():
            _spawn_ingest(getattr(event, "source", None), event)

        source = getattr(event, "source", None)
        chat_id = str(getattr(source, "chat_id", "") or "")
        sender_id = _norm_id(getattr(source, "user_id", ""))

        # 2) explicit mentions keep the direct-command UX untouched
        if _mentions_bot(event):
            return None

        # 3) the person we just offered help to answers ("yes", "go ahead")
        if _consume_offer(chat_id, sender_id):
            return None

        # 4) keyword gate — noise never reaches the LLM
        text = getattr(event, "text", "") or ""
        hit = _keyword_hit(text)
        if hit is None:
            return {"action": "skip", "reason": "assist_no_keyword"}

        _remember_offer(chat_id, sender_id)
        directive = SUGGEST_DIRECTIVE + f"[TRIGGER KEYWORD: {hit}]\n\n"
        return {"action": "rewrite", "text": directive + text}

    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
