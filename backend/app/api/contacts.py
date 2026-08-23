"""Contacts backbone — ingest from Hermes + dashboard search.

- POST /api/contacts/ingest : server-to-server (X-Ingest-Token header),
  upserts sender identities observed by the Hermes bridge/gateway.
  Disabled (401) when CONTACT_INGEST_TOKEN is unset.
- GET  /api/contacts/search : dashboard-auth scoped live search over
  observed contacts (replaces the Evolution-era Redis cache lookup).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import get_db, AsyncSessionLocal
from app.models.models import ObservedContact
from app.api.setup import verify_api_admin
from app.core.auth import get_principal

logger = get_logger(__name__)

router = APIRouter()


class ContactIn(BaseModel):
    phone: str = Field(min_length=5, max_length=64)
    lid: str | None = None
    name: str | None = Field(default=None, max_length=128)
    chat_jid: str | None = None


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
            if row is None:
                row = ObservedContact(
                    tenant_id=tenant_id,
                    wa_phone=c.phone,
                    lid=c.lid or None,
                    display_name=(c.name or None),
                    source_chats=[c.chat_jid] if c.chat_jid else [],
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(row)
            else:
                if c.lid and not row.lid:
                    row.lid = c.lid
                if c.name and c.name != row.display_name:
                    row.display_name = c.name
                if c.chat_jid and c.chat_jid not in (row.source_chats or []):
                    row.source_chats = [*(row.source_chats or []), c.chat_jid]
                row.last_seen_at = now
        await db.commit()
    return len(contacts)


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
            "display_name": r.display_name,
            "lid": r.lid,
            "source_chats": r.source_chats or [],
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]
