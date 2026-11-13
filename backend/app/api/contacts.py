"""Contacts backbone — ingest from Hermes + dashboard search.

- POST /api/contacts/ingest : server-to-server (X-Ingest-Token header),
  upserts sender identities observed by the Hermes bridge/gateway.
  Disabled (401) when CONTACT_INGEST_TOKEN is unset.
- GET  /api/contacts/search : dashboard-auth scoped live search over
  observed contacts (replaces the Evolution-era Redis cache lookup).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import re

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import get_db, AsyncSessionLocal
from app.models.models import ObservedContact
from app.api.setup import verify_api_admin
from app.core.auth import get_principal
from app.services.bridge_config_service import get_bridge_config, set_bridge_config

logger = get_logger(__name__)

router = APIRouter()


def normalize_wa_identity(value: str) -> str:
    """Canonical identity for a WhatsApp sender.

    The Hermes gateway reports senders as JIDs (``919xxx@s.whatsapp.net``,
    sometimes device-qualified ``919xxx:12@s.whatsapp.net``) or as LIDs
    (``2002...@lid``). Storing the raw JID made the one-click allowlist emit
    garbage like ``919xxx@s.whatsapp.net@s.whatsapp.net`` that never matches.
    Phone JIDs collapse to bare digits; LIDs keep their ``@lid`` suffix.
    """
    v = (value or "").strip()
    v = re.sub(r":\d+(?=@)", "", v)          # drop device suffix
    if v.endswith("@s.whatsapp.net") or v.endswith("@c.us"):
        v = v.split("@", 1)[0]
    if "@" not in v:
        v = v.lstrip("+")
    return v


def allowlist_variants(identity: str) -> tuple[str, ...]:
    """Entries to add to the bridge allow_from for one identity."""
    if "@" in identity:          # LID / other JID: exact form only
        return (identity,)
    return (identity, f"{identity}@s.whatsapp.net")


class ContactIn(BaseModel):
    phone: str = Field(min_length=5, max_length=64)
    lid: str | None = None
    name: str | None = Field(default=None, max_length=128)
    chat_jid: str | None = None

    @field_validator("phone")
    @classmethod
    def _norm_phone(cls, v: str) -> str:
        return normalize_wa_identity(v)


class IngestPayload(BaseModel):
    contacts: list[ContactIn]


def _ingest_token_ok(request: Request) -> bool:
    expected = settings.CONTACT_INGEST_TOKEN
    if not expected:
        return False  # no token configured → ingest disabled
    return request.headers.get("X-Ingest-Token") == expected


async def _ensure_default_tenant_id(db: AsyncSession) -> int:
    """Resolve the single-tenant default workspace; create on first use."""
    from app.models.models import Tenant

    res = await db.execute(select(Tenant).where(Tenant.slug == "default"))
    t = res.scalar_one_or_none()
    if t is None:
        t = Tenant(name="Default", slug="default", is_active=True)
        db.add(t)
        await db.flush()
    return t.id


async def _upsert_contacts(contacts: list[ContactIn], tenant_id: int | None = None) -> int:
    """Insert-or-update observed identities for the default tenant."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        if tenant_id is None:
            tenant_id = await _ensure_default_tenant_id(db)
        for c in contacts:
            res = await db.execute(
                select(ObservedContact).where(
                    ObservedContact.tenant_id == tenant_id,
                    ObservedContact.wa_phone == c.phone,
                )
            )
            row = res.scalar_one_or_none()
            lid = c.lid or (c.phone if c.phone.endswith("@lid") else None)
            if row is None:
                row = ObservedContact(
                    tenant_id=tenant_id,
                    wa_phone=c.phone,
                    lid=lid,
                    display_name=(c.name or None),
                    source_chats=[c.chat_jid] if c.chat_jid else [],
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(row)
            else:
                if lid and not row.lid:
                    row.lid = lid
                if c.name and c.name != row.display_name:
                    row.display_name = c.name
                if c.chat_jid and c.chat_jid not in (row.source_chats or []):
                    row.source_chats = [*(row.source_chats or []), c.chat_jid]
                row.last_seen_at = now
        await db.commit()
    return len(contacts)


@router.post("/{contact_id}/allowlist", dependencies=[Depends(verify_api_admin)])
async def allowlist_contact(contact_id: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Merge this contact into the bridge allow_from (bare + s.whatsapp.net)
    and restart hermes so the transport gate picks it up."""
    res = await db.execute(select(ObservedContact).where(ObservedContact.id == contact_id))
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    # Tenant isolation: a dashboard user may only allowlist their own contacts.
    try:
        principal = await get_principal(request)
        if row.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=404, detail="Contact not found")
    except HTTPException as exc:
        if exc.status_code == 404:
            raise
        # legacy single-owner session → default tenant semantics

    identity = normalize_wa_identity(row.wa_phone)
    cfg = await get_bridge_config()
    allow = list(cfg.get("allow_from") or [])
    for variant in allowlist_variants(identity):
        if variant not in allow:
            allow.append(variant)

    result = await set_bridge_config({"allow_from": allow})
    return {
        "contact_id": contact_id,
        "wa_phone": identity,
        "allow_from": result.get("allow_from", allow),
        "restarted": bool(result.get("restarted", False)),
    }


@router.post("/ingest")
async def ingest_contacts(payload: IngestPayload, request: Request) -> dict:
    """Upsert observed WhatsApp identities. Token-guarded; disabled when
    CONTACT_INGEST_TOKEN is not configured."""
    if not _ingest_token_ok(request):
        raise HTTPException(status_code=401, detail="Invalid ingest token")
    accepted = await _upsert_contacts(payload.contacts)
    return {"accepted": accepted}


@router.post("/sync", dependencies=[Depends(verify_api_admin)])
async def sync_contacts(db: AsyncSession = Depends(get_db)) -> dict:
    """Dashboard-compat endpoint: reports how many identities are on file.
    (Legacy Evolution directory sync is gone; ingestion now happens via
    POST /ingest from Hermes.)"""
    from sqlalchemy import func as sa_func

    res = await db.execute(select(sa_func.count()).select_from(ObservedContact))
    count = res.scalar() or 0
    return {"status": "success", "count": count}


@router.get("/search", dependencies=[Depends(verify_api_admin)])
async def search_contacts(
    request: Request,
    q: str = Query("", max_length=128),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Live tenant-scoped search over observed contacts."""
    from fastapi import HTTPException as _HTTPException

    try:
        principal = await get_principal(request)
        tenant_id = principal.tenant_id
    except _HTTPException:
        # Legacy/no-session callers (tests, server-side dashboards) fall back
        # to the default workspace — same semantics as /oauth/authorize.
        tenant_id = await _ensure_default_tenant_id(db)
    like = f"%{q.strip()}%"
    res = await db.execute(
        select(ObservedContact)
        .where(ObservedContact.tenant_id == tenant_id)
        .where(
            (ObservedContact.wa_phone.ilike(like))
            | (ObservedContact.display_name.ilike(like))
            | (ObservedContact.lid.ilike(like))
        )
        .order_by(ObservedContact.last_seen_at.desc())
        .limit(20)
    )
    rows = res.scalars().all()
    return [
        {
            "id": r.id,
            "wa_phone": r.wa_phone,
            # legacy-shape aliases consumed by the dashboard suggestions UI
            "phone": r.wa_phone,
            "name": r.display_name or r.wa_phone,
            "is_group": False,
            "display_name": r.display_name,
            "lid": r.lid,
            "source_chats": r.source_chats or [],
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]
