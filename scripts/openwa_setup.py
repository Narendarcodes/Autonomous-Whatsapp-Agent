#!/usr/bin/env python3
"""One-time OpenWA session bootstrap.

Creates the session, starts it, opens the QR code in your browser, polls
until WhatsApp confirms the link, then registers the webhook with our
FastAPI backend.

Run after `docker-compose up -d openwa` (and FastAPI optional).
"""
import os
import sys
import time
import webbrowser

import httpx

OPENWA_BASE = os.environ.get("OPENWA_BASE_URL", "http://localhost:2785")
OPENWA_KEY = os.environ.get("OPENWA_API_KEY", "openwa_master_key_change_me")
SESSION_ID = os.environ.get("OPENWA_SESSION_ID", "my-session")
WEBHOOK_URL = os.environ.get("OPENWA_WEBHOOK_URL", "http://backend:8000/webhook/openwa")
WEBHOOK_SECRET = os.environ.get("OPENWA_WEBHOOK_SECRET", "")

HEADERS = {"X-Api-Key": OPENWA_KEY, "Content-Type": "application/json"}


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{OPENWA_BASE.rstrip('/')}{path}"
    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        return client.request(method, url, **kwargs)


def wait_for_openwa() -> None:
    print("Waiting for OpenWA to be reachable...")
    for _ in range(60):
        try:
            r = httpx.get(f"{OPENWA_BASE}/health", timeout=3.0)
            if r.status_code == 200:
                print("  OpenWA is up.")
                return
        except Exception:
            pass
        time.sleep(2)
    sys.exit("OpenWA never became reachable. Is it running?")


def create_session() -> None:
    r = _request("POST", "/api/sessions", json={"name": SESSION_ID})
    if r.status_code in (200, 201):
        print(f"  Created session '{SESSION_ID}'.")
    elif r.status_code == 409:
        print(f"  Session '{SESSION_ID}' already exists.")
    else:
        print(f"  Session create returned {r.status_code}: {r.text[:200]}")


def start_session() -> None:
    r = _request("POST", f"/sessions/{SESSION_ID}/start")
    if r.status_code in (200, 201, 204):
        print("  Session started.")
    else:
        print(f"  Session start returned {r.status_code}: {r.text[:200]}")


def poll_status() -> None:
    print("Polling session status (this may take ~30s)...")
    qr_opened = False
    for _ in range(120):
        r = _request("GET", f"/sessions/{SESSION_ID}")
        if r.status_code != 200:
            time.sleep(2)
            continue
        status = (r.json() or {}).get("status", "")
        print(f"  status={status}")
        if status == "QR_READY" and not qr_opened:
            qr_url = f"{OPENWA_BASE}/sessions/{SESSION_ID}/qr"
            print(f"\n  Scan this QR with your phone's WhatsApp:\n    {qr_url}\n")
            try:
                webbrowser.open(qr_url)
            except Exception:
                pass
            qr_opened = True
        if status in ("READY", "AUTHENTICATED"):
            print("  Session linked to your WhatsApp account.")
            return
        time.sleep(3)
    sys.exit("Session did not become READY in time. Rerun this script.")


def register_webhook() -> None:
    payload = {
        "url": WEBHOOK_URL,
        "events": ["message.received", "session.status"],
        "secret": WEBHOOK_SECRET,
    }
    r = _request("POST", f"/sessions/{SESSION_ID}/webhooks", json=payload)
    if r.status_code in (200, 201):
        print(f"  Webhook registered → {WEBHOOK_URL}")
    else:
        print(f"  Webhook register returned {r.status_code}: {r.text[:200]}")


def main() -> None:
    print(f"OpenWA setup for session '{SESSION_ID}' at {OPENWA_BASE}")
    wait_for_openwa()
    create_session()
    start_session()
    poll_status()
    register_webhook()
    print("\nDone. Send yourself a WhatsApp message and watch the backend logs.")


if __name__ == "__main__":
    main()
