"""Evolution API setup endpoints — instance creation, QR display."""
import os
import asyncio
from fastapi import APIRouter, HTTPException, Response, Request, Depends, Form, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
import re
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.auth import SESSION_COOKIE
from app.db.database import AsyncSessionLocal
from app.db.redis_client import cache_get, cache_set
from app.core.logging import get_logger
from app.services.whatsapp_service import INSTANCE_NAME, whatsapp_service, normalize_phone_number, validate_phone_number
from app.models.models import User, AuditLog, ApiKey, CustomerGoogleToken
from app.core.security import decrypt_token
from app.services.preferences_service import preferences_service
from app.services.oauth_service import build_authorization_url
import uuid


router = APIRouter()
logger = get_logger(__name__)


async def is_authenticated(request: Request) -> bool:
    """Valid session cookie → authenticated. Falls back to legacy ADMIN_PASSWORD
    single-user mode only while no DashboardUser rows exist (migration window)."""
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        val = await cache_get(f"dash_session:{sid}")
        if val and ":" in val:
            return True
    # Legacy fallback (pre-multi-tenant deployments)
    if settings.ADMIN_PASSWORD:
        legacy = request.cookies.get("naru_session")
        if legacy:
            lval = await cache_get(f"session:{legacy}")
            if lval == "1":
                return True
    return False


async def verify_api_admin(request: Request):
    if not await is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/login")
async def login_page(request: Request) -> Response:
    if await is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "login.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html")


@router.post("/login")
async def login_submit(
    request: Request,
    password: str = Form(...),
    email: str = Form(""),
) -> Response:
    """Multi-tenant login: email + password against dashboard_users (argon2).

    Falls back to legacy ADMIN_PASSWORD when no DashboardUser exists yet
    or the table itself is missing (pre-migration DB).
    """
    from app.core.auth import create_session, set_session_cookie, SESSION_COOKIE
    from app.core.security import verify_password, needs_rehash, hash_password
    from app.models.models import DashboardUser

    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(DashboardUser).where(DashboardUser.email == email.strip().lower()))
            dash_user = res.scalar_one_or_none()

            if dash_user and verify_password(password, dash_user.password_hash):
                if needs_rehash(dash_user.password_hash):
                    dash_user.password_hash = hash_password(password)
                    await db.commit()
                sid = await create_session(dash_user.tenant_id, dash_user.id)
                response = RedirectResponse(url="/dashboard", status_code=303)
                set_session_cookie(response, sid)
                return response
    except Exception:
        pass  # pre-migration DB — fall through to legacy admin password

    # Legacy single-owner fallback (no dashboard users provisioned yet)
    if not email and password == settings.ADMIN_PASSWORD:
        try:
            async with AsyncSessionLocal() as db:
                count_res = await db.execute(select(func.count()).select_from(DashboardUser))
                if (count_res.scalar() or 0) == 0:
                    session_id = str(uuid.uuid4())
                    await cache_set(f"session:{session_id}", "1", ttl_seconds=86400)
                    response = RedirectResponse(url="/dashboard", status_code=303)
                    response.set_cookie("naru_session", session_id, httponly=True, secure=False, max_age=86400)
                    return response
        except Exception:
            # Pre-migration DB (table missing) — allow legacy admin password
            session_id = str(uuid.uuid4())
            await cache_set(f"session:{session_id}", "1", ttl_seconds=86400)
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie("naru_session", session_id, httponly=True, secure=False, max_age=86400)
            return response

    return RedirectResponse(url="/login?error=true", status_code=303)


@router.get("/logout")
async def logout(request: Request) -> Response:
    from app.core.auth import destroy_session, clear_session_cookie, SESSION_COOKIE

    await destroy_session(request)          # instant Redis revoke
    legacy = request.cookies.get("naru_session")
    if legacy:
        await cache_set(f"session:{legacy}", "", ttl_seconds=1)
    response = RedirectResponse(url="/login", status_code=302)
    clear_session_cookie(response)
    response.delete_cookie("naru_session")
    return response


@router.get("/setup")
async def setup_page(request: Request) -> Response:
    """Main setup UI page."""
    if not await is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "dashboard.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html")


@router.get("/setup/qr-status")
async def qr_status(background_tasks: BackgroundTasks, dependencies=Depends(verify_api_admin)) -> dict:
    info = await whatsapp_service.instance_status()
    if info is None:
        raise HTTPException(status_code=503, detail="Evolution API unreachable")
    
    state = info.get("instance", {}).get("state")
    qr_data = await cache_get("whatsapp:qr_code")
    
    profile_pic = None
    phone = None
    if state == "open":
        phone = await whatsapp_service.get_bot_phone()
        if phone:
            profile_pic = await whatsapp_service.get_profile_picture(phone)
            background_tasks.add_task(whatsapp_service.sync_contacts)
            
            # Dynamically synchronize owner phone to connected primary session
            phone_clean = normalize_phone_number(phone)
            if phone_clean:
                settings.OWNER_WA_PHONE = phone_clean
                async with AsyncSessionLocal() as db:
                    owner_res = await db.execute(select(User).where(User.is_owner == True))
                    current_owner = owner_res.scalar_one_or_none()
                    
                    if not current_owner or current_owner.wa_phone != phone_clean:
                        dup_res = await db.execute(select(User).where(User.wa_phone == phone_clean))
                        dup_user = dup_res.scalar_one_or_none()
                        
                        if current_owner:
                            if dup_user and dup_user.id != current_owner.id:
                                await db.delete(dup_user)
                                await db.flush()
                            current_owner.wa_phone = phone_clean
                            current_owner.has_permission = True
                            await db.commit()
                            logger.info("setup.py qr_status: Dynamically updated owner phone to connected primary phone: %s", phone_clean)
                        else:
                            if dup_user:
                                dup_user.is_owner = True
                                dup_user.has_permission = True
                                await db.commit()
                                logger.info("setup.py qr_status: Dynamically promoted existing user %s to owner", phone_clean)
                            else:
                                new_owner = User(
                                    wa_phone=phone_clean,
                                    is_owner=True,
                                    has_permission=True,
                                    display_name="You (Owner)"
                                )
                                db.add(new_owner)
                                await db.commit()
                                logger.info("setup.py qr_status: Dynamically created owner user from connected phone: %s", phone_clean)

            
    return {
        "state": state,
        "instance": INSTANCE_NAME,
        "has_qr": bool(qr_data),
        "qr_url": f"{settings.BASE_URL}/setup/qr-image",
        "profile_pic": profile_pic,
        "phone": phone
    }


@router.get("/setup/qr-image")
async def qr_image(dependencies=Depends(verify_api_admin)) -> Response:
    """Serve the latest QR code stored from the QRCODE_UPDATED webhook."""
    import base64
    qr_data = await cache_get("whatsapp:qr_code")
    if not qr_data:
        raise HTTPException(
            status_code=404,
            detail="No QR code available yet. The instance is not in connecting state. "
                   "Call POST /setup/create-instance first, then refresh this page within 60s."
        )
    # Evolution sends "data:image/png;base64,<data>" or just the base64 string
    if "base64," in qr_data:
        _, encoded = qr_data.split("base64,", 1)
    else:
        encoded = qr_data
    try:
        img_bytes = base64.b64decode(encoded)
        return Response(content=img_bytes, media_type="image/png")
    except Exception:
        # Maybe it's a plain QR string, not base64 image — return as text
        return Response(content=qr_data.encode(), media_type="text/plain")


@router.post("/setup/create-instance")
async def create_instance(dependencies=Depends(verify_api_admin)) -> dict:
    ok = await whatsapp_service.create_instance()
    if not ok:
        raise HTTPException(status_code=502, detail="Instance creation failed")
    return {"status": "created", "instance": INSTANCE_NAME}


@router.get("/dashboard")
async def dashboard(request: Request) -> Response:
    """Owner's control panel — manage users and permissions."""
    if not await is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "dashboard.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(content=html, media_type="text/html")


class PreferencesPayload(BaseModel):
    bot_name: str
    timezone: str
    bot_mode: str
    quiet_hours_start: str
    quiet_hours_end: str
    stt_provider: str
    tts_provider: str
    tts_voice: str
    owner_phone: str | None = None
    bot_phone: str | None = None
    owner_name: str | None = None

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.match(r"^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$", v):
            raise ValueError("Time must be in 24-hour HH:MM format")
        return v


@router.get("/api/preferences")
async def get_preferences(dependencies=Depends(verify_api_admin)) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.wa_phone == settings.OWNER_WA_PHONE.lstrip("+"))
        )
        owner = result.scalar_one_or_none()
        if not owner:
            return {
                "bot_name": "Jarvis",
                "timezone": settings.TIMEZONE,
                "bot_mode": settings.BOT_RELATIONSHIP_MODE,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "07:00",
                "stt_provider": settings.STT_PROVIDER,
                "tts_provider": settings.TTS_PROVIDER,
                "tts_voice": "Female",
                "owner_phone": settings.OWNER_WA_PHONE,
                "bot_phone": "",
                "owner_name": "You (Owner)",
            }
        
        prefs = await preferences_service.get_all(owner.id)
        return {
            "bot_name": prefs.get("bot_name", "Jarvis"),
            "timezone": owner.timezone,
            "bot_mode": prefs.get("bot_mode", settings.BOT_RELATIONSHIP_MODE),
            "quiet_hours_start": prefs.get("quiet_hours_start", "22:00"),
            "quiet_hours_end": prefs.get("quiet_hours_end", "07:00"),
            "stt_provider": prefs.get("stt_provider", settings.STT_PROVIDER),
            "tts_provider": prefs.get("tts_provider", settings.TTS_PROVIDER),
            "tts_voice": prefs.get("tts_voice", "Female"),
            "owner_phone": prefs.get("owner_phone", settings.OWNER_WA_PHONE),
            "bot_phone": prefs.get("bot_phone", ""),
            "owner_name": prefs.get("owner_name", "You (Owner)"),
        }


@router.post("/api/preferences")
async def save_preferences(payload: PreferencesPayload, dependencies=Depends(verify_api_admin)) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.is_owner == True)
        )
        owner = result.scalar_one_or_none()
        if not owner:
            # Fallback to query by settings.OWNER_WA_PHONE just in case
            result = await db.execute(
                select(User).where(User.wa_phone == settings.OWNER_WA_PHONE.lstrip("+"))
            )
            owner = result.scalar_one_or_none()
            if not owner:
                raise HTTPException(status_code=404, detail="Owner user not found. Scan WhatsApp QR first.")
        

        if payload.bot_mode == "self_chat":
            connected_phone = await whatsapp_service.get_bot_phone()
            if not connected_phone:
                if owner.wa_phone:
                    connected_clean = owner.wa_phone
                else:
                    raise HTTPException(status_code=400, detail="WhatsApp session not connected. Please connect WhatsApp first.")
            else:
                connected_clean = normalize_phone_number(connected_phone)
            
            if owner.wa_phone != connected_clean:
                dup_res = await db.execute(
                    select(User).where(User.wa_phone == connected_clean)
                )
                dup_user = dup_res.scalar_one_or_none()
                if dup_user and dup_user.id != owner.id:
                    await db.delete(dup_user)
                    await db.flush()
            owner.wa_phone = connected_clean
            settings.OWNER_WA_PHONE = connected_clean
            payload.owner_phone = connected_clean
            payload.bot_phone = connected_clean
        elif payload.bot_mode == "dual_number":
            # Note: The agent phone LINKING (QR scan + Evolution API instance creation) is handled
            # by POST /api/agent-phone/request and GET /api/agent-phone/qr-status.
            # This endpoint only validates + saves the agent phone value that was already linked.
            
            connected_phone = await whatsapp_service.get_bot_phone()
            if not connected_phone:
                if owner.wa_phone:
                    connected_clean = owner.wa_phone
                else:
                    raise HTTPException(status_code=400, detail="WhatsApp session not connected. Please connect WhatsApp first.")
            else:
                connected_clean = normalize_phone_number(connected_phone)
            
            if owner.wa_phone != connected_clean:
                dup_res = await db.execute(
                    select(User).where(User.wa_phone == connected_clean)
                )
                dup_user = dup_res.scalar_one_or_none()
                if dup_user and dup_user.id != owner.id:
                    await db.delete(dup_user)
                    await db.flush()
            owner.wa_phone = connected_clean
            settings.OWNER_WA_PHONE = connected_clean
            payload.owner_phone = connected_clean

            
            if not payload.bot_phone:
                raise HTTPException(status_code=400, detail="Agent Phone must be configured")
            
            validation = validate_phone_number(payload.bot_phone)
            if validation["is_valid"]:
                payload.bot_phone = validation["digits"]
            else:
                raise HTTPException(status_code=400, detail=validation.get("error", "Invalid agent phone number."))
        
        await preferences_service.set(owner.id, "bot_name", payload.bot_name)
        await preferences_service.set(owner.id, "bot_mode", payload.bot_mode)
        await preferences_service.set(owner.id, "quiet_hours_start", payload.quiet_hours_start)
        await preferences_service.set(owner.id, "quiet_hours_end", payload.quiet_hours_end)
        await preferences_service.set(owner.id, "stt_provider", payload.stt_provider)
        await preferences_service.set(owner.id, "tts_provider", payload.tts_provider)
        await preferences_service.set(owner.id, "tts_voice", payload.tts_voice)
        await preferences_service.set(owner.id, "owner_phone", payload.owner_phone or "")
        await preferences_service.set(owner.id, "bot_phone", payload.bot_phone or "")
        await preferences_service.set(owner.id, "owner_name", payload.owner_name or "You (Owner)")
        
        owner.timezone = payload.timezone
        await db.commit()
        
        return {"status": "success", "message": "Preferences committed to neural hub"}



@router.get("/api/google-status")
async def google_status(dependencies=Depends(verify_api_admin)) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.is_owner == True)
        )
        owner = result.scalar_one_or_none()
        if not owner or owner.google_access_token_enc is None:
            return {"connected": False}
        return {"connected": True}


@router.get("/api/google-connection-status")
async def google_connection_status(request: Request) -> dict:
    """Tenant-aware Google connection status for the dashboard Connect card.

    Reads the tenant from the dashboard session; checks CustomerGoogleToken
    (the multi-tenant source of truth), NOT the legacy owner User row.
    """
    from app.core.auth import get_principal

    principal = await get_principal(request)  # 401 if no valid session
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CustomerGoogleToken).where(
                CustomerGoogleToken.tenant_id == principal.tenant_id
            )
        )
        tok = result.scalars().first()
        if tok is None:
            return {"connected": False}
        return {
            "connected": True,
            "email": tok.email,
            "scopes": (tok.scopes or "").split(),
        }


@router.post("/setup/disconnect")
async def disconnect_whatsapp(dependencies=Depends(verify_api_admin)) -> dict:
    ok = await whatsapp_service.delete_instance()
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to delete instance in openwa adapter")
    return {"status": "disconnected"}


@router.get("/api/telemetry")
async def get_telemetry(dependencies=Depends(verify_api_admin)) -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
        logs = result.scalars().all()
        formatted_logs = []
        for log in logs:
            formatted_logs.append({
                "action": log.action,
                "time": log.created_at.strftime("%H:%M:%S") if log.created_at else "",
                "details": str(log.details or {}),
            })
        return {
            "status": "online",
            "logs": formatted_logs
        }


@router.get("/api/system-status")
async def get_system_status(dependencies=Depends(verify_api_admin)) -> dict:
    """Return the physical VPS/host status (CPU, RAM, Disk)."""
    import psutil
    import platform
    try:
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        disk_path = "/" if platform.system() != "Windows" else os.path.splitdrive(os.getcwd())[0] + "\\"
        disk = psutil.disk_usage(disk_path).percent
        return {
            "status": "online",
            "cpu": cpu,
            "ram": ram,
            "disk": disk
        }
    except Exception as e:
        logger.error("Failed to get system status: %s", e)
        return {
            "status": "error",
            "cpu": 0.0,
            "ram": 0.0,
            "disk": 0.0
        }



# ==================== AGENT PHONE SETUP ENDPOINTS ====================


@router.get("/api/agent/status")
async def get_agent_status(dependencies=Depends(verify_api_admin)) -> dict:
    """Return live connection state of the agent (secondary) Evolution API instance.
    
    Returns:
      - state: "open" | "connecting" | "close" | "unknown" | "not_configured"
      - bot_phone: configured agent phone number (empty if not configured)
      - message: human-readable description
    """
    from app.services.agent_instance_service import agent_instance_service

    # Check if any agent phone is configured first
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if not owner:
            return {"state": "not_configured", "bot_phone": "", "message": "Owner not connected yet."}
        bot_phone = await preferences_service.get(owner.id, "bot_phone") or ""
        bot_mode = await preferences_service.get(owner.id, "bot_mode") or settings.BOT_RELATIONSHIP_MODE

    if not bot_phone or bot_mode != "dual_number":
        return {"state": "not_configured", "bot_phone": "", "message": "No agent phone configured."}

    # Query the live connection state from Evolution API
    try:
        status = await agent_instance_service.get_agent_instance_status()
        state = status.get("state", "unknown")
        msg_map = {
            "open": f"Agent +{bot_phone} is connected and active.",
            "connecting": f"Agent +{bot_phone} is connecting. Please wait...",
            "close": f"Agent +{bot_phone} is disconnected. Please reconnect.",
            "unknown": f"Agent status unknown. The session may have been removed.",
        }
        return {
            "state": state,
            "bot_phone": bot_phone,
            "message": msg_map.get(state, f"Agent state: {state}"),
        }
    except Exception as exc:
        logger.error("get_agent_status error: %s", exc)
        return {
            "state": "unknown",
            "bot_phone": bot_phone,
            "message": "Unable to reach Evolution API to check agent status.",
        }




class AgentPhoneRequestPayload(BaseModel):
    phone: str


@router.post("/api/agent-phone/request")
async def request_agent_phone(payload: AgentPhoneRequestPayload, dependencies=Depends(verify_api_admin)) -> dict:
    """Validate agent phone, create agent Evolution API instance, return QR code."""
    from app.services.agent_instance_service import agent_instance_service
    
    # --- Phone Validation ---
    validation = validate_phone_number(payload.phone)
    if not validation["is_valid"]:
        raise HTTPException(status_code=400, detail=validation.get("error", "Invalid phone number."))
    
    phone_digits = validation["digits"]
    country_code = validation.get("country_code", "")
    
    # --- Check for existing configured bot_phone ---
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner not found. Connect WhatsApp first.")
        owner_id = owner.id
    
    existing_bot_phone = await preferences_service.get(owner_id, "bot_phone")
    existing_clean = normalize_phone_number(existing_bot_phone) if existing_bot_phone else ""
    
    # --- Store pending phone ---
    await cache_set("whatsapp:agent_pending_phone", phone_digits, ttl_seconds=600)
    
    # --- Create or reconnect agent instance ---
    # Disconnect/delete any existing agent session instance first so we start fresh
    await agent_instance_service.delete_agent_instance()
    await asyncio.sleep(2.0)  # Give Evolution API time to cleanly release the session
    
    qr = await agent_instance_service.create_agent_instance()
    if not qr:
        raise HTTPException(status_code=502, detail="Failed to create agent WhatsApp instance. Check Evolution API.")
    
    # --- Forward QR instructions to owner's WhatsApp and target agent's WhatsApp ---
    try:
        owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
        if owner_phone:
            qr_msg_owner = (
                f"🤖 *Naru Agent Setup*\n\n"
                f"Please link the Agent Chat interface (+{phone_digits}) under the Owner account (+{owner_phone}) by scanning the QR code in the Naru dashboard.\n\n"
                f"Guidelines:\n"
                f"1️⃣ Open WhatsApp on the *second phone* (+{phone_digits})\n"
                f"2️⃣ Tap ⋮ *Menu* → *Linked Devices* → *Link a Device*\n"
                f"3️⃣ Scan the QR code shown in your Naru dashboard\n\n"
                f"⏳ The QR expires in ~5 minutes. Check your dashboard for the QR."
            )
            await whatsapp_service.send_text(owner_phone, qr_msg_owner, force_primary=True)
    except Exception as e:
        logger.warning("Failed to send QR notification to owner: %s", e)

    try:
        owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
        qr_msg_agent = (
            f"🤖 *Naru Agent Setup*\n\n"
            f"Please link this Agent Chat interface (+{phone_digits}) under the Owner account (+{owner_phone}) by scanning the QR code in the Naru dashboard.\n\n"
            f"Guidelines:\n"
            f"1️⃣ Open WhatsApp on this phone (+{phone_digits})\n"
            f"2️⃣ Tap ⋮ *Menu* → *Linked Devices* → *Link a Device*\n"
            f"3️⃣ Scan the QR code shown in your Naru dashboard\n\n"
            f"⏳ The QR expires in ~5 minutes. Check your dashboard for the QR."
        )
        await whatsapp_service.send_text(phone_digits, qr_msg_agent, force_primary=True)
    except Exception as e:
        logger.warning("Failed to send QR notification to target agent phone: %s", e)
    
    return {
        "status": "qr_ready",
        "qr": qr,
        "phone": phone_digits,
        "country_code": country_code,
        "existing_bot_phone": existing_clean or None,
        "message": f"QR code ready. Open WhatsApp on +{phone_digits} → Linked Devices → Link a Device → scan QR."
    }


@router.get("/api/agent-phone/qr-status")
async def agent_phone_qr_status(dependencies=Depends(verify_api_admin)) -> dict:
    """Poll agent instance connection status. On connect: save bot_phone, whitelist, notify owner."""
    from app.services.agent_instance_service import agent_instance_service
    
    status = await agent_instance_service.get_agent_instance_status()
    state = status.get("state", "unknown")
    
    if state == "open":
        # Instance connected — fetch the linked phone
        linked_phone = await agent_instance_service.get_agent_phone()
        pending_phone = await cache_get("whatsapp:agent_pending_phone")
        phone_to_use = linked_phone or pending_phone or ""
        
        if phone_to_use:
            phone_clean = normalize_phone_number(phone_to_use) or phone_to_use
            
            # If pending_phone is not set in Redis, it means we already completed the setup flow
            # for this link request (or it wasn't initiated).
            if not pending_phone:
                return {"state": "open", "phone": phone_clean, "message": f"Agent number +{phone_clean} is now active!"}
            
            # Immediately clear the pending cache key to lock/prevent concurrent or duplicate poll calls from executing this block
            await cache_set("whatsapp:agent_pending_phone", "", ttl_seconds=1)
            
            async with AsyncSessionLocal() as db:
                owner_res = await db.execute(select(User).where(User.is_owner == True))
                owner = owner_res.scalar_one_or_none()
                if owner:
                    # Save bot_phone preference
                    await preferences_service.set(owner.id, "bot_phone", phone_clean)
                    await preferences_service.set(owner.id, "bot_mode", "dual_number")
                    
                    # Whitelist the agent number in DB
                    agent_res = await db.execute(select(User).where(User.wa_phone == phone_clean))
                    agent_user = agent_res.scalar_one_or_none()
                    if not agent_user:
                        agent_user = User(
                            wa_phone=phone_clean,
                            is_owner=False,
                            has_permission=True,
                            display_name="Agent Chat"
                        )
                        db.add(agent_user)
                    else:
                        agent_user.has_permission = True
                        agent_user.display_name = "Agent Chat"
                    await db.commit()
                    
                    # Send connection notifications to owner (X) and start agent chat (Y)
                    try:
                        owner_phone = settings.OWNER_WA_PHONE.lstrip("+")
                        if owner_phone:
                            # 1. Send confirmation message in self-chat on Account X
                            await whatsapp_service.send_text(
                                owner_phone,
                                f"the account +{phone_clean} is connected as a chat interface",
                                force_primary=True
                            )
                            
                            # 2. Send greeting message from Account Y (agent) to Account X (owner) to start the agent chat
                            greeting_msg = (
                                f"🤖 *Naru AI Agent Connected*\n\n"
                                f"Hello! I am your AI assistant. I have successfully connected as your agent chat interface (+{phone_clean}) and am ready to receive your commands and handle your tasks!"
                            )
                            await agent_instance_service.send_via_agent(owner_phone, greeting_msg)
                    except Exception as e:
                        logger.warning("Failed to send agent connection notifications: %s", e)
                    
                    logger.info("Agent phone %s linked and whitelisted", phone_clean)
                    
                    return {"state": "open", "phone": phone_clean, "message": f"Agent number +{phone_clean} is now active!"}
        
        return {"state": "open", "phone": "", "message": "Connected but phone not yet resolved."}
    
    # Not connected yet — return state + fresh QR
    return {
        "state": state,
        "qr": status.get("qr", ""),
        "message": "Waiting for QR scan..."
    }


@router.post("/api/agent-phone/cancel")
async def cancel_agent_phone(dependencies=Depends(verify_api_admin)) -> dict:
    """Cancel the pending agent phone link request. 
    Resets the bot_mode to self_chat and clears bot_phone ONLY if no existing bot phone was configured
    or if we are not in the middle of a replacement request.
    """
    from app.services.agent_instance_service import agent_instance_service
    
    # Drop any pending/newly created agent session instance
    await agent_instance_service.delete_agent_instance()
    
    # Get pending phone from cache
    pending_phone = await cache_get("whatsapp:agent_pending_phone")
    pending_phone_clean = normalize_phone_number(pending_phone) if pending_phone else ""
    
    message = "Agent setup canceled."
    # Reset preferences to self_chat ONLY if we didn't have a previously successfully saved bot_phone.
    # If we had a previously saved bot_phone, we keep the preferences as they were!
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if owner:
            old_bot_phone = await preferences_service.get(owner.id, "bot_phone")
            old_bot_phone_clean = normalize_phone_number(old_bot_phone) if old_bot_phone else ""
            
            # If there was no previous bot phone configured, or if we are NOT replacing (i.e. pending_phone is empty or matches old_bot_phone)
            is_replacing = old_bot_phone_clean and pending_phone_clean and pending_phone_clean != old_bot_phone_clean
            
            if not is_replacing:
                await preferences_service.set(owner.id, "bot_phone", "")
                await preferences_service.set(owner.id, "bot_mode", "self_chat")
                
                # Find and disable the Agent Chat permission status
                result_agent = await db.execute(
                    select(User).where(User.display_name == "Agent Chat")
                )
                agent_users = result_agent.scalars().all()
                for au in agent_users:
                    au.has_permission = False
                await db.commit()
                message = "Agent setup canceled. Switched to Self Chat mode."
            else:
                message = f"Agent setup canceled. Preserved previous agent number +{old_bot_phone_clean}."
            
    # Clear caches
    await cache_set("whatsapp:agent_pending_phone", "", ttl_seconds=1)
    
    return {"status": "success", "message": message}


# ==================== DYNAMIC API KEY MANAGEMENT ENDPOINTS ====================

class ApiKeyCreatePayload(BaseModel):
    name: str
    provider: str
    api_key: str
    is_active: bool = True


class ApiKeyUpdatePayload(BaseModel):
    name: str
    provider: str
    api_key: str | None = None
    is_active: bool = True


@router.get("/api/keys")
async def list_keys(dependencies=Depends(verify_api_admin)) -> list[dict]:
    response = []
    
    # 1. Fetch system keys from settings
    system_keys = [
        {"name": "System Default Groq Key", "provider": "groq", "val": settings.GROQ_API_KEY},
        {"name": "System Default Google AI Key", "provider": "gemini", "val": settings.GOOGLE_AI_API_KEY},
        {"name": "System Default OpenRouter Key", "provider": "openrouter", "val": settings.OPENROUTER_API_KEY},
        {"name": "System Default GitHub Token", "provider": "github", "val": settings.GITHUB_TOKEN},
        {"name": "System Default Nvidia NIM Key", "provider": "nvidia", "val": settings.NVIDIA_NIM_API_KEY},
        {"name": "System Default Anthropic Key", "provider": "anthropic", "val": settings.ANTHROPIC_API_KEY},
    ]
    
    for sys_key in system_keys:
        val = sys_key["val"]
        if val:
            if len(val) > 8:
                masked = val[:4] + "••••••••" + val[-4:]
            else:
                masked = "••••••••"
            response.append({
                "id": f"system_{sys_key['provider']}",
                "name": sys_key["name"],
                "provider": sys_key["provider"],
                "api_key_masked": masked,
                "is_active": True,
                "is_system": True,
                "created_at": ""
            })
            
    # 2. Fetch custom database keys
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
        keys = result.scalars().all()
        
        for key in keys:
            try:
                decrypted = decrypt_token(key.api_key_enc)
                if len(decrypted) > 8:
                    masked = decrypted[:4] + "••••••••" + decrypted[-4:]
                else:
                    masked = "••••••••"
            except Exception:
                masked = "error_decrypting"
                
            response.append({
                "id": key.id,
                "name": key.name,
                "provider": key.provider,
                "api_key_masked": masked,
                "is_active": key.is_active,
                "is_system": False,
                "created_at": key.created_at.isoformat() if key.created_at else "",
            })
    return response


@router.post("/api/keys")
async def create_key(payload: ApiKeyCreatePayload, dependencies=Depends(verify_api_admin)) -> dict:
    from app.core.security import encrypt_token
    from app.services.litellm_service import rebuild_litellm_config
    
    if not payload.name or not payload.provider or not payload.api_key:
        raise HTTPException(status_code=400, detail="Missing required fields: name, provider, api_key")
        
    async with AsyncSessionLocal() as db:
        new_key = ApiKey(
            name=payload.name,
            provider=payload.provider.lower(),
            api_key_enc=encrypt_token(payload.api_key),
            is_active=payload.is_active
        )
        db.add(new_key)
        await db.commit()
        await db.refresh(new_key)
        
        # Trigger rebuild
        await rebuild_litellm_config(db)
        
        return {"status": "success", "message": f"API key '{payload.name}' saved.", "id": new_key.id}


@router.put("/api/keys/{key_id}")
async def update_key(key_id: str, payload: ApiKeyUpdatePayload, dependencies=Depends(verify_api_admin)) -> dict:
    if key_id.startswith("system_"):
        raise HTTPException(status_code=403, detail="Cannot modify system default keys")
        
    from app.core.security import encrypt_token
    from app.services.litellm_service import rebuild_litellm_config
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="API key not found")
            
        key.name = payload.name
        key.provider = payload.provider.lower()
        key.is_active = payload.is_active
        
        if payload.api_key:
            key.api_key_enc = encrypt_token(payload.api_key)
            
        await db.commit()
        
        # Trigger rebuild
        await rebuild_litellm_config(db)
        
        return {"status": "success", "message": f"API key '{payload.name}' updated."}


@router.delete("/api/keys/{key_id}")
async def delete_key(key_id: str, dependencies=Depends(verify_api_admin)) -> dict:
    if key_id.startswith("system_"):
        raise HTTPException(status_code=403, detail="Cannot delete system default keys")
        
    from app.services.litellm_service import rebuild_litellm_config
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="API key not found")
            
        await db.delete(key)
        await db.commit()
        
        # Trigger rebuild
        await rebuild_litellm_config(db)
        
        return {"status": "success", "message": "API key deleted."}


@router.get("/api/mcp-status")
async def get_mcp_status(dependencies=Depends(verify_api_admin)) -> dict:
    """Parse hermes_config.yaml to determine which advanced MCP servers are mounted."""
    import yaml
    config_path = "/app/hermes_config.yaml"
    if not os.path.exists(config_path):
        # Fallback to backend/hermes_config.yaml if running locally outside docker
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "hermes_config.yaml")
        
    servers = []
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
                
                # Check for new mcp_servers dict format first
                mcp_servers = config_data.get("mcp_servers", {})
                if isinstance(mcp_servers, dict):
                    for name, cfg in mcp_servers.items():
                        if isinstance(cfg, dict):
                            servers.append({"name": name, **cfg})
                
                # Fallback to old mcp.servers list format
                if not servers:
                    mcp_section = config_data.get("mcp", {})
                    if isinstance(mcp_section, dict):
                        servers = mcp_section.get("servers", [])
        except Exception as e:
            logger.error("Failed to parse hermes_config.yaml: %s", e)
            
    return {"servers": servers}


@router.get("/api/connectors")
async def get_connectors(dependencies=Depends(verify_api_admin)) -> dict:
    """Return status of all native connectors."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner user not found")
        
        from app.services.connector_service import connector_service
        status_list = await connector_service.list_connectors_status(owner.id)
        return {"connectors": status_list}


@router.get("/api/connectors/{connector_id}")
async def get_connector_credentials(
    connector_id: str,
    dependencies=Depends(verify_api_admin)
) -> dict:
    """Return decrypted credentials for a specific connector."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner user not found")
        
        from app.services.connector_service import connector_service
        try:
            creds = await connector_service.get_credentials(owner.id, connector_id)
            return {"credentials": creds}
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))


class SaveConnectorPayload(BaseModel):
    data: dict


@router.post("/api/connectors/{connector_id}")
async def save_connector(
    connector_id: str, 
    payload: SaveConnectorPayload, 
    dependencies=Depends(verify_api_admin)
) -> dict:
    """Save credentials for a connector and trigger Hermes hot reload."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_owner == True))
        owner = result.scalar_one_or_none()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner user not found")
        
        from app.services.connector_service import connector_service
        from app.services.docker_manager import docker_manager
        
        try:
            await connector_service.save_credentials(owner.id, connector_id, payload.data)
            
            # Hot reload Hermes container asynchronously to apply keys
            asyncio.create_task(docker_manager.restart_hermes_agent())
            
            return {
                "status": "success", 
                "message": f"Credentials for {connector_id} updated. Reloading Hermes..."
            }
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        except Exception as exc:
            logger.error(f"Failed to save connector {connector_id}: {exc}")
            raise HTTPException(status_code=500, detail="Failed to save credentials")


class SaveSoulPayload(BaseModel):
    soul: str


@router.get("/api/setup/soul")
async def get_agent_soul(dependencies=Depends(verify_api_admin)) -> dict:
    """Read SOUL.md from the Hermes data volume."""
    import os
    soul_path = "/opt/hermes_data/SOUL.md"
    if not os.path.exists(soul_path):
        # Fallback to backend/SOUL.md if running locally outside docker
        soul_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "SOUL.md")
        
    soul_content = ""
    if os.path.exists(soul_path):
        try:
            with open(soul_path, "r", encoding="utf-8") as f:
                soul_content = f.read()
        except Exception as e:
            logger.error("Failed to read SOUL.md: %s", e)
            
    return {"soul": soul_content}


@router.post("/api/setup/soul")
async def save_agent_soul(payload: SaveSoulPayload, dependencies=Depends(verify_api_admin)) -> dict:
    """Write user's edited soul/personality to SOUL.md in Hermes volume."""
    import os
    soul_path = "/opt/hermes_data/SOUL.md"
    
    # Ensure parent directory exists (e.g. running locally outside docker, fallback to project root)
    if not os.path.exists(os.path.dirname(soul_path)):
        soul_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "SOUL.md")
        
    try:
        with open(soul_path, "w", encoding="utf-8") as f:
            f.write(payload.soul)
            
        # Set proper ownership for the file (UID/GID 1000 for hermes user)
        if "/opt/hermes_data" in soul_path:
            try:
                os.chown(soul_path, 1000, 1000)
            except Exception as chown_exc:
                logger.warning("Could not set chown for SOUL.md: %s", chown_exc)
                
        return {"status": "success", "message": "Agent soul written to core memories."}
    except Exception as exc:
        logger.error("Failed to write SOUL.md: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to write agent soul: {exc}")


