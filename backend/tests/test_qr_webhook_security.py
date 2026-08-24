"""#2: QR webhooks must reject unsigned/tampered requests.

A forged QR cached here would be scanned by an admin during pairing —
handing the WhatsApp session to the attacker. Both QR endpoints therefore
enforce the same HMAC-SHA256 gate as /webhook/openwa.

Redis is intercepted at the module boundary so these stay pure-unit.
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

SECRET = "test-webhook-secret-qr"


def signed_headers(payload: dict) -> dict[str, str]:
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Evolution-Signature": sig}


def qr_payload() -> dict:
    filler = "A" * 160  # _extract_qr heuristic: long string OR 'base64' marker
    return {"event": "qrcode.updated", "data": {"base64": f"data:image/png;base64,{filler}"}}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "OPENWA_WEBHOOK_SECRET", SECRET)
    stored: list[tuple[str, str]] = []

    async def fake_cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
        stored.append((key, value))

    import app.db.redis_client as rc

    monkeypatch.setattr(rc, "cache_set", fake_cache_set)
    yield stored


def test_unsigned_qr_request_rejected(client):
    c = TestClient(app)
    resp = c.post("/webhook/qr", content=json.dumps(qr_payload()).encode(),
                  headers={"Content-Type": "application/json"})
    assert resp.status_code == 401
    assert client == []  # nothing cached


def test_tampered_qr_signature_rejected(client):
    c = TestClient(app)
    evil = json.dumps({"data": {"base64": "attacker-qr"}}).encode()
    resp = c.post("/webhook/qr", content=evil, headers=signed_headers(qr_payload()))
    assert resp.status_code == 401


def test_signed_qr_payload_caches(client):
    c = TestClient(app)
    payload = qr_payload()
    resp = c.post("/webhook/qr", content=json.dumps(payload).encode(),
                  headers=signed_headers(payload))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    keys = [k for k, _ in client]
    assert keys == ["whatsapp:qr_code"]


def test_signed_but_not_qr_shaped_returns_no_qr(client):
    c = TestClient(app)
    payload = {"event": "qrcode.updated", "data": {"foo": "short"}}
    resp = c.post("/webhook/agent-qr", content=json.dumps(payload).encode(),
                  headers=signed_headers(payload))
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_qr"


def test_agent_qr_endpoint_also_gated(client):
    c = TestClient(app)
    resp = c.post("/webhook/agent-qr", content=json.dumps(qr_payload()).encode(),
                  headers={"Content-Type": "application/json"})
    assert resp.status_code == 401
