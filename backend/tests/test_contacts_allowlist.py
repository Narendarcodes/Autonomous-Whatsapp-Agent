"""Allowlisting an observed contact straight from the dashboard.

POST /api/contacts/{id}/allowlist merges the contact's phone (bare +
s.whatsapp.net variant) into the bridge allow_from via set_bridge_config,
which also restarts hermes so the gate takes effect.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.config import settings
from app.api.setup import verify_api_admin
from app.api.contacts import _upsert_contacts, ContactIn


TOKEN = "ingest-secret"


@pytest.fixture
async def client(test_engine, monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_INGEST_TOKEN", TOKEN)
    app.dependency_overrides[verify_api_admin] = lambda: {"tenant_id": 1}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(verify_api_admin, None)


async def _seed(phone="9195777777", name="Seed"):
    return await _upsert_contacts([ContactIn(phone=phone, name=name)])


@pytest.mark.asyncio
async def test_allowlist_merges_phone_and_jid_variant(client, monkeypatch):
    await _seed("9195777777")

    captured = {}

    async def fake_get():
        return {"allow_from": ["916300354385"], "mode": "self-chat"}

    async def fake_set(update, restart=True):
        captured.update(update)
        captured["restart"] = restart
        return {**update, "restarted": True}

    from app.api import contacts as contacts_mod
    monkeypatch.setattr(contacts_mod, "get_bridge_config", fake_get)
    monkeypatch.setattr(contacts_mod, "set_bridge_config", fake_set)

    # resolve the contact id
    from sqlalchemy import select
    from app.models.models import ObservedContact
    async with contacts_mod.AsyncSessionLocal() as db:
        row = (await db.execute(select(ObservedContact))).scalars().first()
        contact_id = row.id

    resp = await client.post(f"/api/contacts/{contact_id}/allowlist")
    assert resp.status_code == 200
    body = resp.json()
    assert "9195777777" in body["allow_from"]
    assert "9195777777@s.whatsapp.net" in body["allow_from"]
    assert "916300354385" in body["allow_from"]          # existing preserved
    assert captured.get("restart") is True               # hermes restarted so gate applies

@pytest.mark.asyncio
async def test_allowlist_idempotent_second_call(client, monkeypatch):
    await _seed("9195666666")

    async def fake_get():
        return {"allow_from": ["9195666666", "9195666666@s.whatsapp.net"]}

    async def fake_set(update, restart=True):
        return {**update, "restarted": True}

    from app.api import contacts as contacts_mod
    monkeypatch.setattr(contacts_mod, "get_bridge_config", fake_get)
    monkeypatch.setattr(contacts_mod, "set_bridge_config", fake_set)

    from sqlalchemy import select
    from app.models.models import ObservedContact
    async with contacts_mod.AsyncSessionLocal() as db:
        row = (await db.execute(select(ObservedContact))).scalars().first()
        contact_id = row.id

    resp = await client.post(f"/api/contacts/{contact_id}/allowlist")
    assert resp.status_code == 200
    assert resp.json()["allow_from"].count("9195666666") == 1  # no duplicates


@pytest.mark.asyncio
async def test_allowlist_unknown_contact_404(client):
    resp = await client.post("/api/contacts/00000000-0000-0000-0000-000000000000/allowlist")
    assert resp.status_code == 404
