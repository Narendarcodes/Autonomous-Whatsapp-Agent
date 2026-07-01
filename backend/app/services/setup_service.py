"""Setup orchestration — guides users through WhatsApp setup flow."""
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import User
from app.services.whatsapp_service import whatsapp_service
from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)

SETUP_STATES = {
    "awaiting_oauth": f"Google OAuth required. Visit {settings.BASE_URL}/oauth/authorize to authenticate.",
    "awaiting_whatsapp": f"WhatsApp QR scan required. Visit {settings.BASE_URL}/setup to scan QR code.",
    "ready": "All setup complete! You can now use the AI assistant."
}


async def check_setup_status(db: AsyncSession, user: User) -> str:
    """Check what setup steps remain for this user.
    
    Returns one of: 'awaiting_whatsapp', 'awaiting_oauth', or 'ready'
    """
    has_whatsapp = user.wa_phone is not None
    has_google_oauth = user.google_access_token_enc is not None
    
    if not has_whatsapp:
        return "awaiting_whatsapp"
    if not has_google_oauth:
        return "awaiting_oauth"
    return "ready"


async def send_setup_prompt(user_phone: str, status: str) -> None:
    """Send setup guidance message to the user."""
    msg = f"""
🤖 *Setup Required*

{SETUP_STATES[status]}

*Setup Options:*
1. Visit the web dashboard: {settings.BASE_URL}/setup
2. Or reply 'OAUTH' to complete Google authentication
3. Reply 'STATUS' to check setup progress

Let me know when you're done!
"""
    await whatsapp_service.send_text(user_phone, msg)


async def handle_setup_command(db: AsyncSession, user: User, command: str) -> str:
    """Handle user setup commands ('OAUTH', 'STATUS', etc.)"""
    cmd = command.strip().upper()
    
    if cmd == "STATUS":
        status = await check_setup_status(db, user)
        return f"Setup Status: {status}\n{SETUP_STATES[status]}"
    
    if cmd == "OAUTH":
        status = await check_setup_status(db, user)
        if status != "awaiting_oauth":
            return "Google OAuth is not required right now. Your setup is complete!"
        return f"Visit {settings.BASE_URL}/oauth/authorize to authenticate with Google."
    
    if cmd == "SETUP":
        status = await check_setup_status(db, user)
        if status == "ready":
            return "You're all set! No setup needed."
        return f"Visit {settings.BASE_URL}/setup to complete setup. Status: {status}"
    
    return None  # Not a setup command
