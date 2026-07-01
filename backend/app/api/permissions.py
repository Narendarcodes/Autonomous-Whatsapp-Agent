import asyncio
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_openwa_signature
from app.db.database import AsyncSessionLocal
from app.models.models import User
from app.api.setup import verify_api_admin
from app.services.whatsapp_service import normalize_phone_number

router = APIRouter(dependencies=[Depends(verify_api_admin)])
logger = get_logger(__name__)


async def _get_owner(db: AsyncSession) -> User | None:
    """Get the bot owner user."""
    result = await db.execute(
        select(User).where(User.wa_phone == settings.OWNER_WA_PHONE.lstrip("+"))
    )
    return result.scalar_one_or_none()


@router.get("/permissions")
async def list_permissions() -> dict:
    """List all users and their permission status (owner only)."""
    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")

        # Get bot mode, bot_phone, and owner name from preferences
        from app.services.preferences_service import preferences_service
        prefs = await preferences_service.get_all(owner.id)
        bot_mode = prefs.get("bot_mode", settings.BOT_RELATIONSHIP_MODE)
        owner_name = prefs.get("owner_name", "You (Owner)")
        bot_phone = prefs.get("bot_phone", "")
        if bot_phone:
            bot_phone = normalize_phone_number(bot_phone)

        # Get all users sorted by created_at
        result = await db.execute(
            select(User).order_by(User.created_at.desc())
        )
        users = result.scalars().all()

        from app.services.whatsapp_service import whatsapp_service

        async def resolve_user_details(u: User) -> tuple[User, str, str | None, bool]:
            display_name = u.display_name
            needs_commit = False
            try:
                # Resolve from WhatsApp contacts dynamically
                contact_info = await whatsapp_service.get_contact_info(u.wa_phone)
                resolved_name = None
                if contact_info:
                    resolved_name = contact_info.get("name") or contact_info.get("pushName") or contact_info.get("pushname")
                
                if resolved_name:
                    resolved_name = resolved_name.lstrip("~").strip()
                    if resolved_name != display_name:
                        u.display_name = resolved_name
                        display_name = resolved_name
                        needs_commit = True
                else:
                    is_bot_phone = (bot_phone and u.wa_phone == bot_phone)
                    if not u.is_owner and not is_bot_phone and not display_name:
                        default_name = f"User {u.wa_phone[-4:]}"
                        u.display_name = default_name
                        display_name = default_name
                        needs_commit = True
            except Exception as e:
                logger.error(f"Failed to lookup contact info for {u.wa_phone}: {e}")
            if not display_name:
                display_name = f"User {u.wa_phone[-4:]}"
            
            # Fetch profile picture concurrently
            profile_pic = None
            try:
                profile_pic = await whatsapp_service.get_profile_picture(u.wa_phone)
            except Exception as e:
                logger.error(f"Failed to fetch profile picture for {u.wa_phone}: {e}")
                
            return u, display_name, profile_pic, needs_commit

        tasks = [resolve_user_details(u) for u in users]
        results = await asyncio.gather(*tasks)

        users_data = []
        db_needs_commit = False
        for u, display_name, profile_pic, needs_commit in results:
            if needs_commit:
                db_needs_commit = True
            
            # Override display name for the owner or bot_phone
            is_bot_phone = (bot_phone and u.wa_phone == bot_phone)
            if is_bot_phone:
                display_name = "Agent Chat"
            elif u.is_owner:
                display_name = owner_name

            users_data.append({
                "phone": u.wa_phone,
                "display_name": display_name,
                "profile_pic": profile_pic,
                "is_owner": u.is_owner or is_bot_phone,
                "has_permission": u.has_permission,
                "trust_level": u.trust_level,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "status": "agent" if is_bot_phone else ("owner" if u.is_owner else ("active" if u.has_permission else "pending")),
            })
            
        if db_needs_commit:
            await db.commit()

        return {
            "owner_phone": settings.OWNER_WA_PHONE,
            "bot_mode": bot_mode,
            "users": users_data,
        }


@router.post("/permissions/grant")
async def grant_permission(phone: str = Query(..., description="User phone to grant permission")) -> dict:
    """Grant permission to a user (owner only)."""
    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")

        # Find or create user
        phone_normalized = normalize_phone_number(phone)
        if not phone_normalized:
            raise HTTPException(status_code=400, detail="Invalid phone number format")

        result = await db.execute(select(User).where(User.wa_phone == phone_normalized))
        user = result.scalar_one_or_none()

        if not user:
            is_group = phone.endswith("@g.us") or "-" in phone or "-" in phone_normalized or (len(phone_normalized) == 18 or (len(phone_normalized) > 15 and phone_normalized.startswith("1203")))
            default_name = f"Group {phone_normalized[-4:]}" if is_group else f"User {phone_normalized[-4:]}"
            
            user = User(
                wa_phone=phone_normalized,
                is_owner=False,
                has_permission=True,
                display_name=default_name
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info(f"Created and whitelisted user {phone_normalized}")
        else:
            if user.is_owner:
                return {"status": "skip", "message": "Owner already has permission"}

        user.has_permission = True
        await db.commit()
        logger.info(f"Granted permission to {phone}")

        return {
            "status": "granted",
            "phone": phone,
            "display_name": user.display_name or f"User {phone[-4:]}",
        }


@router.post("/permissions/revoke")
async def revoke_permission(phone: str = Query(..., description="User phone to revoke permission")) -> dict:
    """Revoke permission from a user (owner only)."""
    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")

        # Find user
        phone_normalized = normalize_phone_number(phone)
        result = await db.execute(select(User).where(User.wa_phone == phone_normalized))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail=f"User {phone_normalized} not found")

        if user.is_owner:
            raise HTTPException(status_code=400, detail="Cannot revoke permission from owner")

        user.has_permission = False
        await db.commit()
        logger.info(f"Revoked permission from {phone}")

        return {
            "status": "revoked",
            "phone": phone,
            "display_name": user.display_name or f"User {phone[-4:]}",
        }


@router.post("/permissions/set_trust")
async def set_trust_level(
    phone: str = Query(..., description="User phone to set trust level"),
    trust_level: str = Query(..., description="Trust level: 'trusted' or 'untrusted'")
) -> dict:
    """Set the trust level of a user contact."""
    if trust_level not in ("trusted", "untrusted"):
        raise HTTPException(status_code=400, detail="Invalid trust_level. Must be 'trusted' or 'untrusted'")

    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")

        # Find user
        phone_normalized = normalize_phone_number(phone)
        result = await db.execute(select(User).where(User.wa_phone == phone_normalized))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail=f"User {phone_normalized} not found")

        if user.is_owner:
            raise HTTPException(status_code=400, detail="Cannot change trust level of owner")

        user.trust_level = trust_level
        await db.commit()
        logger.info(f"Updated trust level for {phone} to {trust_level}")

        return {
            "status": "success",
            "phone": phone,
            "trust_level": trust_level,
        }


@router.post("/permissions/reset")
async def reset_permissions() -> dict:
    """Reset all whitelisted contacts. Deletes all users except the owner and the connected agent chat."""
    from sqlalchemy import delete
    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")
        
        from app.services.preferences_service import preferences_service
        bot_phone = await preferences_service.get(owner.id, "bot_phone")
        bot_phone_clean = normalize_phone_number(bot_phone) if bot_phone else ""
        
        stmt = delete(User).where(User.is_owner == False)
        if bot_phone_clean:
            stmt = stmt.where(User.wa_phone != bot_phone_clean)
            
        await db.execute(stmt)
        await db.commit()
        
        logger.info("Access control list reset successfully.")
        return {"status": "success", "message": "Access control list reset successfully"}


@router.post("/permissions/delete")
async def delete_permission(phone: str = Query(..., description="User phone to delete")) -> dict:
    """Delete a whitelisted user contact completely from the database (owner only)."""
    async with AsyncSessionLocal() as db:
        owner = await _get_owner(db)
        if not owner:
            raise HTTPException(status_code=403, detail="Owner not found")

        phone_normalized = normalize_phone_number(phone)
        if not phone_normalized:
            raise HTTPException(status_code=400, detail="Invalid phone number format")

        result = await db.execute(select(User).where(User.wa_phone == phone_normalized))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail=f"User {phone_normalized} not found")

        if user.is_owner:
            raise HTTPException(status_code=400, detail="Cannot delete the owner contact")

        # Check if the user is the connected agent
        from app.services.preferences_service import preferences_service
        bot_phone = await preferences_service.get(owner.id, "bot_phone")
        bot_phone_clean = normalize_phone_number(bot_phone) if bot_phone else ""
        if bot_phone_clean and phone_normalized == bot_phone_clean:
            raise HTTPException(status_code=400, detail="Cannot delete the active agent contact")

        await db.delete(user)
        await db.commit()
        logger.info(f"Deleted user {phone_normalized} from database")

        return {
            "status": "deleted",
            "phone": phone,
        }



@router.post("/api/contacts/sync")
async def sync_contacts_endpoint() -> dict:
    """Explicitly trigger WhatsApp contact synchronization."""
    from app.services.whatsapp_service import whatsapp_service
    contacts = await whatsapp_service.sync_contacts()
    return {"status": "success", "count": len(contacts)}


@router.get("/api/contacts/search")
async def search_contacts(q: str = Query("", description="Search query")) -> list[dict]:
    """Search and autocomplete whitelisted user contacts."""
    from app.db.redis_client import cache_get
    from app.services.whatsapp_service import whatsapp_service
    import json

    q_clean = q.strip()
    if not q_clean:
        return []

    contacts_json = await cache_get("whatsapp:contacts_cache")
    if not contacts_json:
        # Sync on demand
        contacts = await whatsapp_service.sync_contacts()
    else:
        try:
            contacts = json.loads(contacts_json)
        except Exception:
            contacts = []

    q_lower = q_clean.lower()
    results = []
    for c in contacts:
        if q_lower in c["phone"] or q_lower in c["name"].lower():
            results.append(c)
    return results[:10]
