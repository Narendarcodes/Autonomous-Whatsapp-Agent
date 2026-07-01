#!/usr/bin/env python3
"""First-run WhatsApp setup for Evolution API.

1. Creates the WhatsApp instance via Evolution API
2. Polls until QR code is ready
3. Opens QR in browser — scan with your DEDICATED BOT NUMBER
4. Polls until connected
5. Registers webhook back to our FastAPI

Run after:  docker-compose up -d postgres redis litellm hermes openwa backend
"""
import os
import sys
import time
import webbrowser
from pathlib import Path

import httpx

# Load .env from backend/ directory so we don't need env vars set manually
_env_file = Path(__file__).parent.parent / "backend" / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

# Evolution API runs on container port 8080, mapped to host port 2785.
# When running this script on the HOST machine, always use localhost:2785.
_base = os.environ.get("OPENWA_BASE_URL", "http://openwa:8080")
EVOLUTION_BASE = _base.replace("openwa:8080", "localhost:2785")
if "openwa" in EVOLUTION_BASE:
    EVOLUTION_BASE = "http://localhost:2785"

API_KEY = os.environ.get("OPENWA_API_KEY", "openwa_master_key_change_me")
INSTANCE = os.environ.get("OPENWA_SESSION_ID", "my-session")
WEBHOOK_URL = os.environ.get("OPENWA_WEBHOOK_URL", "http://backend:8000/webhook/openwa")
BACKEND_URL = os.environ.get("BASE_URL", "http://localhost:8000")
WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE", "QRCODE_UPDATED"]

HDR = {"apikey": API_KEY, "Content-Type": "application/json"}


def _get(path: str) -> httpx.Response:
    return httpx.get(f"{EVOLUTION_BASE.rstrip('/')}{path}", headers=HDR, timeout=10)


def _post(path: str, body: dict) -> httpx.Response:
    return httpx.post(f"{EVOLUTION_BASE.rstrip('/')}{path}", headers=HDR, json=body, timeout=10)


def _qr_webhook_url() -> str:
    if WEBHOOK_URL.endswith("/webhook/openwa"):
        return WEBHOOK_URL.removesuffix("/webhook/openwa") + "/webhook/qr"
    return WEBHOOK_URL.rstrip("/") + "/qr"


def _webhook_payload() -> dict:
    return {
        "enabled": True,
        "url": WEBHOOK_URL,
        "byEvents": False,
        "base64": True,
        "events": WEBHOOK_EVENTS,
    }


def wait_for_evolution() -> None:
    print("Waiting for Evolution API…")
    for _ in range(60):
        try:
            r = httpx.get(f"{EVOLUTION_BASE.rstrip('/')}/", timeout=3, headers=HDR)
            if r.status_code == 200:
                print(f"  Evolution API v{r.json().get('version', '?')} is up.")
                return
        except Exception:
            pass
        time.sleep(2)
    sys.exit("Evolution API never became reachable — is the container running?")


def create_instance() -> None:
    payload = {
        "instanceName": INSTANCE,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "webhook": _webhook_payload(),
        "qrcodeWebhook": {"url": _qr_webhook_url()},
    }
    r = _post("/instance/create", payload)
    if r.status_code in (200, 201):
        print(f"  Instance '{INSTANCE}' created.")
    elif r.status_code == 403 or "already" in r.text.lower():
        print(f"  Instance '{INSTANCE}' already exists.")
    else:
        print(f"  Create returned {r.status_code}: {r.text[:200]}")
    configure_webhook()


def connect_instance() -> None:
    try:
        r = _get(f"/instance/connect/{INSTANCE}")
        if r.status_code == 200:
            print("  Connection/QR refresh requested.")
        else:
            print(f"  Connect returned {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        print(f"  Connect request failed: {exc}")


def poll_for_qr() -> None:
    print("Polling for QR code (this takes ~10-20s)…")
    connect_instance()
    for _ in range(60):
        try:
            r = _get(f"/instance/connectionState/{INSTANCE}")
            if r.status_code == 200:
                state = r.json().get("instance", {}).get("state", "")
                print(f"  state={state}")
                if state in ("open", "CONNECTED"):
                    print("\n  Already connected! Skipping QR.")
                    return
                qr_ready = False
                try:
                    qr_status = httpx.get(f"{BACKEND_URL.rstrip('/')}/setup/qr-status", timeout=5).json()
                    qr_ready = bool(qr_status.get("has_qr"))
                except Exception:
                    pass
                if qr_ready or state not in ("", "close"):
                    # Try to open QR
                    qr_url = f"{BACKEND_URL}/setup/qr-image"
                    print(f"\n  QR code available. Opening browser: {qr_url}")
                    print("  IMPORTANT: Scan with your DEDICATED BOT NUMBER, not your primary number.")
                    try:
                        webbrowser.open(qr_url)
                    except Exception:
                        pass
                    poll_connected()
                    return
        except Exception:
            pass
        time.sleep(3)
    print("Could not get QR state. Check docker logs whatsapp_openwa")


def poll_connected() -> None:
    print("Waiting for WhatsApp connection (scan QR now)…")
    for _ in range(120):
        try:
            r = _get(f"/instance/connectionState/{INSTANCE}")
            if r.status_code == 200:
                state = r.json().get("instance", {}).get("state", "")
                print(f"  state={state}")
                if state in ("open", "CONNECTED"):
                    print("\n  WhatsApp connected!")
                    return
        except Exception:
            pass
        time.sleep(3)
    sys.exit("Connection timed out. Restart and try again.")


def verify_webhook() -> None:
    print("Verifying webhook registration…")
    r = _get(f"/webhook/find/{INSTANCE}")
    if r.status_code == 200:
        data = r.json()
        url = data.get("url") or "(none)"
        print(f"  Webhook URL: {url}")
        if "webhook/openwa" in url:
            print("  Webhook is correctly set.")
        else:
            print("  Webhook URL doesn't match — updating…")
            configure_webhook()
    else:
        print(f"  Could not read webhook: {r.status_code}")


def configure_webhook() -> None:
    r = _post(f"/webhook/set/{INSTANCE}", {"webhook": _webhook_payload()})
    if r.status_code in (200, 201):
        print("  Webhook configured.")
    else:
        print(f"  Webhook configure returned {r.status_code}: {r.text[:200]}")


def main() -> None:
    print("=" * 55)
    print(" WhatsApp Agent — First-Run Setup (Evolution API)")
    print("=" * 55)
    print(f"  Instance : {INSTANCE}")
    print(f"  API base : {EVOLUTION_BASE}")
    print(f"  Webhook  : {WEBHOOK_URL}")
    print()

    wait_for_evolution()
    create_instance()
    poll_for_qr()
    verify_webhook()

    print("\nDone! Send a WhatsApp message to the bot number and watch:")
    print(f"  docker logs -f whatsapp_calendar_agent_worker")


if __name__ == "__main__":
    main()
