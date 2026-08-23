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
import threading
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


def _ingest_configured() -> bool:
    return bool(_env("CONTACT_INGEST_TOKEN")) and bool(_env("OMNIWA_BACKEND_URL", "http://backend:8000") or "")


def _post_ingest(source, event) -> None:
    """Fire-and-forget identity push to the omniWA backend."""
    base = _env("OMNIWA_BACKEND_URL", "http://backend:8000").rstrip("/")
    payload = {
        "contacts": [
            {
                "phone": str(getattr(source, "user_id", "") or ""),
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
    """True when the message tags the bot or replies to a bot message."""
    raw = getattr(event, "raw_message", None) or {}
    bot_ids = {str(b) for b in (raw.get("botIds") or [])}
    if not bot_ids:
        return False
    if {str(m) for m in (raw.get("mentionedIds") or [])} & bot_ids:
        return True
    quoted_participant = str(raw.get("quotedParticipant") or "")
    return quoted_participant in bot_ids


def _keyword_hit(text: str) -> str | None:
    low = (text or "").lower()
    for kw in _keywords():
        if kw in low:
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

        # 2) explicit mentions keep the direct-command UX untouched
        if _mentions_bot(event):
            return None

        # 3) keyword gate — noise never reaches the LLM
        text = getattr(event, "text", "") or ""
        hit = _keyword_hit(text)
        if hit is None:
            return {"action": "skip", "reason": "assist_no_keyword"}

        directive = SUGGEST_DIRECTIVE + f"[TRIGGER KEYWORD: {hit}]\n\n"
        return {"action": "rewrite", "text": directive + text}

    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
