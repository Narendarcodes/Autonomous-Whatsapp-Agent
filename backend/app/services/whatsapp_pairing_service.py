"""WhatsApp pairing service — lets the dashboard show Hermes' live Baileys QR.

How it works:
- The Hermes gateway spawns the Node Baileys bridge; while unpaired, the
  bridge prints rotating QR codes (~20s validity) into its log file at
  <HERMES_DATA_DIR>/platforms/whatsapp/bridge.log.
- The backend container mounts the same volume (HERMES_DATA_DIR), so we can
  extract the latest complete QR block and expose it to the dashboard.
- Pairing state = presence of session/creds.json, cross-checked against the
  gateway runtime status (/health/detailed on the Hermes API server).
"""
import asyncio
import os
import re
import time
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_QR_HEADER_RE = re.compile(r"Scan this QR code")
_BLOCK_CHARS = set("█▄▀ ")
# The bridge rotates QRs ~every 20s; a log untouched longer than this holds only
# expired art. Serving it caused the 2026-08-23 stale-QR incident.
_QR_MAX_AGE_SECONDS = 90


def _bridge_log_age_seconds(bridge_log_path: str) -> float | None:
    """Seconds since bridge.log was last written, or None if unreadable."""
    try:
        return max(0.0, time.time() - os.path.getmtime(bridge_log_path))
    except OSError:
        return None


def whatsapp_paths(hermes_data_dir: str | None = None) -> dict:
    base = hermes_data_dir or getattr(settings, "HERMES_DATA_DIR", "/opt/hermes_data")
    root = f"{base.rstrip('/')}/platforms/whatsapp"
    return {
        "root": root,
        "bridge_log": f"{root}/bridge.log",
        "creds": f"{root}/session/creds.json",
    }


def extract_latest_qr(bridge_log_path: str) -> str | None:
    """Return the most recent COMPLETE QR block (unicode ▄▀█ art) from the log.

    A block starts at the '📱 Scan this QR code...' header and ends at the
    last consecutive line made only of block characters. Partial blocks
    (log read mid-write) are ignored.
    """
    try:
        with open(bridge_log_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None

    best = None
    i = 0
    n = len(lines)
    while i < n:
        if _QR_HEADER_RE.search(lines[i]):
            j = i + 1
            # Skip blank padding lines between the header and the art.
            while j < n and not lines[j].strip():
                j += 1
            block: list[str] = []
            last_nonblank = -1
            while j < n:
                stripped = lines[j].strip()
                if not stripped:
                    # Blank line inside/after the block: continue only if the
                    # next line is more art, else stop here.
                    nxt = lines[j + 1].strip() if j + 1 < n else ""
                    if last_nonblank >= 0 and nxt and set(nxt) <= _BLOCK_CHARS:
                        block.append("")
                        j += 1
                        continue
                    break
                if set(stripped) <= _BLOCK_CHARS:
                    block.append(stripped)
                    last_nonblank = len(block) - 1
                    j += 1
                else:
                    break
            if last_nonblank >= 0:
                best = "\n".join(block[: last_nonblank + 1])
            i = j
        else:
            i += 1
    return best


def is_paired(hermes_data_dir: str | None = None) -> bool:
    import os

    return os.path.exists(whatsapp_paths(hermes_data_dir)["creds"])


async def fetch_hermes_health() -> dict | None:
    """Best-effort fetch of the gateway runtime status (platform states)."""
    url = f"{getattr(settings, 'HERMES_HEALTH_URL', 'http://hermes:8642').rstrip('/')}/health/detailed"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:  # noqa: BLE001 — status is best-effort by design
        logger.debug("Hermes health fetch failed: %s", exc)
    return None


def _whatsapp_error_code(health: dict | None) -> str:
    if not health:
        return ""
    platform = (health.get("platforms") or {}).get("whatsapp") or {}
    return str(platform.get("error_code") or "")


async def get_pairing_state(
    *,
    _health: dict | None = None,
    hermes_data_dir: str | None = None,
    paired_override: bool | None = None,
) -> dict:
    """Snapshot for the dashboard status poller.

    Returns: paired, qr_available, qr(always None here — use /qr endpoint),
    bot_number(if known later), error_code, checked_at.
    """
    paths = whatsapp_paths(hermes_data_dir)
    paired = (
        paired_override if paired_override is not None else await asyncio.to_thread(is_paired, hermes_data_dir)
    )
    health = _health if _health is not None else await fetch_hermes_health()

    state = {
        "paired": bool(paired),
        "qr_available": False,
        "bot_number": "",
        "error_code": "" if paired else _whatsapp_error_code(health),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not paired:
        age = await asyncio.to_thread(_bridge_log_age_seconds, paths["bridge_log"])
        if age is not None and age <= _QR_MAX_AGE_SECONDS:
            qr = await asyncio.to_thread(extract_latest_qr, paths["bridge_log"])
            state["qr_available"] = bool(qr)
    return state


def read_latest_qr(hermes_data_dir: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (qr_block_text, captured_at_iso) or (None, None).

    Only offers a QR while bridge.log is actively written; captured_at mirrors
    the log mtime (the QR's true birth time), never the read time.
    """
    path = whatsapp_paths(hermes_data_dir)["bridge_log"]
    age = _bridge_log_age_seconds(path)
    if age is None or age > _QR_MAX_AGE_SECONDS:
        return None, None
    qr = extract_latest_qr(path)
    if not qr:
        return None, None
    captured = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return qr, captured.isoformat()


class WhatsappPairingService:
    """Instance facade used by the API layer (easy to stub in tests)."""

    async def get_pairing_state(self) -> dict:
        return await get_pairing_state()

    def read_latest_qr(self) -> tuple[str, str] | tuple[None, None]:
        return read_latest_qr()


pairing_service = WhatsappPairingService()
