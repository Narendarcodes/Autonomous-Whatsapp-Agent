"""omniwa-guard — Hermes gateway plugin: omniWA's safety layer on WhatsApp.

Hooks
  pre_llm_call          guest-mode directive for non-owners (the owner's
                        memory is in every session's context)
  pre_gateway_dispatch  blocklist, per-sender rate limit, group-member gate,
                        prompt-injection wrapper for non-owner messages
  pre_tool_call         tool policy: non-owners get low-risk tools only, the
                        owner loses shell/file tools inside groups, nobody can
                        touch credential files, print env, or run destructive /
                        exfiltrating shell commands
  transform_tool_result flags injected instructions inside fetched content
                        (web pages, emails, files) before the model reads it
  transform_llm_output  removes API keys / tokens / private keys from every
                        WhatsApp reply (and applies group redaction in groups,
                        since only one transform_llm_output result is used)

Only WhatsApp turns are affected; CLI, cron and api_server are untouched.

Configuration (hermes container env, from backend/.env):
  OWNER_WA_PHONE / OMNIWA_OWNER_IDS   owner phone(s) and LID(s), csv
  OMNIWA_BLOCKED_SENDERS              csv of blocked phones / LIDs
  OMNIWA_RATE_LIMIT_PER_MIN           default 8, 0 disables (owner exempt)
  OMNIWA_GROUP_GUESTS                 "deny" (default): in an allowlisted group
                                      only the owner and allowlisted people are
                                      heard; "allow": everyone, guest tools only
Runtime file written by the backend: /opt/data/omniwa_guard.json
  {"blocked": [...], "owner_ids": [...]}
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
logger = logging.getLogger("omniwa-guard")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load("omniwa_guard_core", _HERE / "guard_core.py")


def _privacy_redactor():
    p = _HERE.parent / "omniwa-group-privacy" / "__init__.py"
    if not p.exists():
        return None
    try:
        return _load("omniwa_group_privacy_shared", p).redact_group_text
    except Exception:
        return None


# ------------------------------------------------------------ gateway gate

def _is_whatsapp(source) -> bool:
    return "whatsapp" in str(getattr(source, "platform", "") or "").lower()


def gateway_verdict(event):
    """Shared by omniwa-assist so both plugins agree whatever their load order.
    Memoised on the event so the rate limiter counts a message once."""
    if event is None:
        return None
    cached = getattr(event, "_omniwa_guard_verdict", "unset")
    if cached != "unset":
        return cached
    verdict = _compute_verdict(event)
    try:
        setattr(event, "_omniwa_guard_verdict", verdict)
    except Exception:
        pass
    return verdict


def _compute_verdict(event):
    source = getattr(event, "source", None)
    if source is None or not _is_whatsapp(source):
        return None
    ids = (getattr(source, "user_id", None), getattr(source, "user_id_alt", None))
    group = getattr(source, "chat_type", "") == "group" or str(getattr(source, "chat_id", "")).endswith("@g.us")

    if core.is_blocked(*ids):
        return {"action": "skip", "reason": "omniwa_guard_blocked"}

    owner = core.is_owner(*ids)
    if not owner:
        if core.rate_limited(core.digits(ids[0] or ids[1] or "")):
            return {"action": "skip", "reason": "omniwa_guard_rate_limited"}
        guests = (core._env("OMNIWA_GROUP_GUESTS", "deny") or "deny").lower()
        if group and guests != "allow" and not core.is_allowed_person(*ids):
            return {"action": "skip", "reason": "omniwa_guard_group_member_not_allowlisted"}
        text = getattr(event, "text", "") or ""
        hit = core.injection_hit(text)
        if hit:
            logger.warning("omniwa-guard: possible prompt injection from %s: %r", core.digits(ids[0]), hit)
            return {"action": "rewrite", "text": core.UNTRUSTED_DIRECTIVE.format(hit=hit) + text}
    return None


# --------------------------------------------------------- session helpers

def _session():
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return None
    keys = ("HERMES_SESSION_PLATFORM", "HERMES_SESSION_CHAT_ID", "HERMES_SESSION_CHAT_TYPE",
            "HERMES_SESSION_USER_ID", "HERMES_SESSION_USER_ID_ALT", "HERMES_SESSION_ACTOR_ID")
    try:
        return {k: (get_session_env(k) or "") for k in keys}
    except Exception:
        return None


def _whatsapp_turn(s) -> bool:
    return bool(s) and "whatsapp" in s["HERMES_SESSION_PLATFORM"].lower()


def _group_turn(s) -> bool:
    return s["HERMES_SESSION_CHAT_TYPE"].lower() == "group" or s["HERMES_SESSION_CHAT_ID"].endswith("@g.us")


def _owner_turn(s) -> bool:
    # A turn merged from several senders has no single actor: treat as non-owner.
    actor = s["HERMES_SESSION_ACTOR_ID"]
    ids = (actor,) if actor else (s["HERMES_SESSION_USER_ID"], s["HERMES_SESSION_USER_ID_ALT"])
    return core.is_owner(*ids)


# ------------------------------------------------------------------ hooks

def register(ctx):
    def _pre_gateway_dispatch(event=None, **kwargs):
        return gateway_verdict(event)

    def _pre_tool_call(tool_name=None, args=None, **kwargs):
        s = _session()
        if not _whatsapp_turn(s):
            return None
        reason = core.tool_verdict(tool_name or "", args or {}, owner=_owner_turn(s), group=_group_turn(s))
        if reason is None:
            return None
        logger.warning("omniwa-guard blocked %s: %s", tool_name, reason)
        return {"action": "block",
                "message": f"Blocked by omniWA guard: {reason}. Tell the user plainly you can't do that here."}

    def _transform_tool_result(tool_name=None, result=None, **kwargs):
        s = _session()
        if not _whatsapp_turn(s) or not isinstance(result, str):
            return None
        scrubbed = core.scrub_secrets(result)
        text = scrubbed if scrubbed is not None else result
        hit = core.injection_hit(text)
        if hit:
            return core.TOOL_RESULT_WARNING.format(hit=hit) + text
        return scrubbed

    def _transform_llm_output(response_text=None, **kwargs):
        s = _session()
        if not _whatsapp_turn(s) or not isinstance(response_text, str):
            return None
        out = core.scrub_secrets(response_text)
        text = out if out is not None else response_text
        if _group_turn(s):
            redact = _privacy_redactor()
            if redact is not None:
                red = redact(text)
                if red is not None:
                    text = red
        return text if text != response_text else None

    def _pre_llm_call(**kwargs):
        # Hermes runs one profile, so the owner's memory/profile is in every
        # session's context. Tell the model whose it is when a guest is talking.
        s = _session()
        if not _whatsapp_turn(s) or _owner_turn(s):
            return None
        return {"context": core.GUEST_DIRECTIVE}

    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
