"""Contacts backbone — Phase A.

Hermes observes WhatsApp senders (DMs + patched group flow) and pushes them
here so the dashboard can search people and allowlist them. Ingest is
token-guarded (server-to-server); search is dashboard-auth scoped.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.config import settings


TOKEN = "ingest-secret"


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_INGEST_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture
async def client(test_engine):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _auth(token):
    return {"X-Ingest-Token": token}


@pytest.mark.asyncio
async def test_rejects_missing_token(client):
    resp = await client.post("/api/contacts/ingest", json={"contacts": []})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rejects_wrong_token(client, token):
    resp = await client.post(
        "/api/contacts/ingest", json={"contacts": []}, headers={"X-Ingest-Token": "nope"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_disabled_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_INGEST_TOKEN", "")
    resp = await client.post("/api/contacts/ingest", json={"contacts": []})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ingests_new_contacts(client, token):
    payload = {
        "contacts": [
            {"phone": "9195xxxx1111", "name": "Asha", "chat_jid": "9195xxxx1111@s.whatsapp.net"},
            {"phone": "9195xxxx2222", "lid": "200348111265793@lid", "name": "Ravi",
             "chat_jid": "120363406613211534@g.us"},
        ]
    }
    resp = await client.post("/api/contacts/ingest", json=payload, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2}


@pytest.mark.asyncio
async def test_upsert_updates_existing_not_duplicate(client, token):
    body = [{"phone": "9195xxxx3333", "name": "Old Name"}]
    await client.post("/api/contacts/ingest", json={"contacts": body}, headers=_auth(token))

    updated = [{"phone": "9195xxxx3333", "name": "New Name", "lid": "999@lid"}]
    await client.post("/api/contacts/ingest", json={"contacts": updated}, headers=_auth(token))

    # search should return exactly ONE row carrying the newest name + lid
    from app.api.setup import verify_api_admin

    app.dependency_overrides[verify_api_admin] = lambda: {"tenant_id": 1}
    try:
        resp = await client.get("/api/contacts/search", params={"q": "9195xxxx3333"})
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["display_name"] == "New Name"
        assert rows[0]["lid"] == "999@lid"
        # legacy-shape aliases consumed by the dashboard suggestions UI
        assert rows[0]["phone"] == "9195xxxx3333"
        assert rows[0]["name"] == "New Name"
        assert "id" in rows[0]
    finally:
        app.dependency_overrides.pop(verify_api_admin, None)


@pytest.mark.asyncio
async def test_group_chats_accumulate_in_source_chats(client, token):
    base = [{"phone": "9195xxxx4444", "name": "Veeru", "chat_jid": "aaa@g.us"}]
    await client.post("/api/contacts/ingest", json={"contacts": base}, headers=_auth(token))
    more = [{"phone": "9195xxxx4444", "name": "Veeru", "chat_jid": "bbb@g.us"}]
    await client.post("/api/contacts/ingest", json={"contacts": more}, headers=_auth(token))

    from app.api.setup import verify_api_admin

    app.dependency_overrides[verify_api_admin] = lambda: {"tenant_id": 1}
    try:
        resp = await client.get("/api/contacts/search", params={"q": "Veeru"})
        rows = resp.json()
        assert len(rows) == 1
        assert sorted(rows[0]["source_chats"]) == ["aaa@g.us", "bbb@g.us"]
    finally:
        app.dependency_overrides.pop(verify_api_admin, None)
